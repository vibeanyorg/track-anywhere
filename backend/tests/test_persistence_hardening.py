from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event

from track_anywhere.drafts import DraftTransaction
from track_anywhere.errors import ValidationError
from track_anywhere.ledger import Transaction, credit_posting, debit_posting, legacy_signed_posting
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend/app/track_anywhere"


def test_public_app_code_does_not_directly_enable_legacy_signed_writes():
    sources = {
        path: path.read_text()
        for path in BACKEND_APP.rglob("*.py")
        if path.name != "ledger.py"
    }

    assert all("allow_legacy_signed_postings=True" not in source for source in sources.values())
    assert all("allow_legacy_signed=True" not in source for source in sources.values())
    assert all("legacy_signed_posting(" not in source for source in sources.values())


def test_business_app_code_does_not_directly_construct_raw_postings():
    allowed_raw_posting_construction_files = {
        BACKEND_APP / "ledger.py",
        BACKEND_APP / "storage_draft_reads.py",
        BACKEND_APP / "storage_loaders.py",
        BACKEND_APP / "storage_repositories/transactions.py",
        BACKEND_APP / "storage_repositories/workflow.py",
    }
    sources = {
        path: path.read_text()
        for path in BACKEND_APP.rglob("*.py")
        if path not in allowed_raw_posting_construction_files
    }

    assert all("Posting(" not in source for source in sources.values())


def test_service_startup_does_not_persist_domain_defaults(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"

    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")

    assert service.owner_token.startswith("ta_")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("select count(*) from credentials").fetchone()[0] == 0
        assert connection.execute("select count(*) from ledger_books").fetchone()[0] == 0
        assert connection.execute("select count(*) from audit_events").fetchone()[0] == 0
        assert connection.execute("select count(*) from app_state").fetchone()[0] == 0


def test_record_transaction_idempotency_replays_without_occurred_at_after_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    cash, _ = first.create_account(
        token,
        {"name": "Retry Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="retry-cash",
    )
    food, _ = first.create_account(
        token,
        {"name": "Retry Food", "type": "expense", "currency": "CNY"},
        idempotency_key="retry-food",
    )
    payload = {
        "amount": "10",
        "currency": "CNY",
        "from_account_id": cash.account_id,
        "to_account_id": food.account_id,
        "purpose": "secret lunch",
    }

    transaction, replay = first.record_transaction(token, payload, idempotency_key="retry-lunch")
    assert replay is False

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    replayed, replay = second.record_transaction(token, payload, idempotency_key="retry-lunch")

    assert replay is True
    assert replayed["transaction_id"] == transaction.transaction_id
    assert replayed["purpose"] == "secret lunch"
    assert replayed["memo"] == ""
    assert second.account_balance(token, cash.account_id)["official_balance"]["amount"] == "90"


def test_idempotency_receipts_redact_transaction_memo_snapshots(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    database_url = f"sqlite:///{database_path}"
    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Receipt Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="receipt-cash",
    )
    food, _ = service.create_account(
        token,
        {"name": "Receipt Food", "type": "expense", "currency": "CNY"},
        idempotency_key="receipt-food",
    )

    service.record_transaction(
        token,
        {
            "amount": "10",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": food.account_id,
            "purpose": "meal",
            "memo": "Alice card ending 1234",
        },
        idempotency_key="receipt-lunch",
    )

    with sqlite3.connect(database_path) as connection:
        receipt_json = connection.execute(
            """
            select result
            from idempotency_receipts
            where operation = 'ledger.transaction.record'
            """
        ).fetchone()[0]

    assert "Alice card ending 1234" not in receipt_json
    assert '"memo": "[REDACTED]"' in receipt_json


def test_fund_flows_replay_before_stale_version_checks(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Fund Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="fund-retry-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Fund Food", "type": "expense", "currency": "CNY"},
        idempotency_key="fund-retry-expense",
    )
    fund, _ = service.create_fund(
        token,
        {"name": "Lunch Fund", "currency": "CNY"},
        idempotency_key="fund-retry-create",
    )

    allocation_payload = {
        "fund_id": fund.fund_id,
        "source_account_id": cash.account_id,
        "amount": "40",
        "currency": "CNY",
        "expected_version": fund.version,
        "memo": "set aside lunch money",
    }
    allocated, replay = service.allocate_fund(token, allocation_payload, idempotency_key="fund-retry-allocate")
    replayed_allocation, replay = service.allocate_fund(token, allocation_payload, idempotency_key="fund-retry-allocate")

    assert replay is True
    assert replayed_allocation["transaction"].transaction_id == allocated["transaction"].transaction_id
    assert service.account_balance(token, cash.account_id)["official_balance"]["amount"] == "60"

    spend_payload = {
        "fund_id": fund.fund_id,
        "expense_account_id": expense.account_id,
        "amount": "15",
        "currency": "CNY",
        "expected_version": fund.version,
        "memo": "spend lunch money",
    }
    spent, replay = service.spend_fund(token, spend_payload, idempotency_key="fund-retry-spend")
    replayed_spend, replay = service.spend_fund(token, spend_payload, idempotency_key="fund-retry-spend")

    assert replay is True
    assert replayed_spend["transaction"].transaction_id == spent["transaction"].transaction_id
    assert service.account_balance(token, fund.account_id)["official_balance"]["amount"] == "25"


def test_confirmed_postings_are_immutable_after_initial_persist(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {"name": "Immutable Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="immutable-cash",
    )
    transaction, _ = service.adjust_balance(
        token,
        {
            "account_id": account.account_id,
            "amount": "100",
            "currency": "CNY",
            "purpose": "seed balance",
        },
        idempotency_key="immutable-balance",
    )
    adjustment_account_id = transaction.postings[1].account_id

    transaction.postings.extend(
        [
            debit_posting(account.account_id, Decimal("100"), "CNY"),
            credit_posting(adjustment_account_id, Decimal("100"), "CNY"),
        ]
    )

    with pytest.raises(ValidationError, match="confirmed transaction postings are immutable"):
        service._commit_ledger_change(transaction)

    with sqlite3.connect(database_path) as connection:
        posting_count = connection.execute(
            "select count(*) from postings where transaction_id = ?",
            (transaction.transaction_id,),
        ).fetchone()[0]

    assert posting_count == 2


def test_legacy_signed_side_inference_matches_immutability_check(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Legacy Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="legacy-immutable-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Legacy Food", "type": "expense", "currency": "CNY"},
        idempotency_key="legacy-immutable-food",
    )
    service.ledger.accounts[cash.account_id] = cash
    service.ledger.accounts[expense.account_id] = expense
    transaction = service.ledger.create_transaction(
        "legacy transaction",
        [
            legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY"),
            legacy_signed_posting(expense.account_id, Decimal("12"), "CNY"),
        ],
        allow_legacy_signed=True,
    )

    service._commit_ledger_change(transaction, allow_legacy_signed_postings=True)
    service._commit_ledger_change(transaction, allow_legacy_signed_postings=True)

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select amount, side, amount_semantics
            from postings
            where transaction_id = ?
            order by position
            """,
            (transaction.transaction_id,),
        ).fetchall()

    assert rows == [("-12", "credit", "legacy_signed"), ("12", "debit", "legacy_signed")]


def test_service_reversal_rewrites_legacy_signed_postings_to_debit_credit(tmp_path):
    database_path = tmp_path / "legacy-reversal-canonical.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Legacy Reverse Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="legacy-reverse-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Legacy Reverse Food", "type": "expense", "currency": "CNY"},
        idempotency_key="legacy-reverse-food",
    )
    service.ledger.accounts[cash.account_id] = cash
    service.ledger.accounts[expense.account_id] = expense
    transaction = service.ledger.create_transaction(
        "legacy transaction",
        [
            legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY"),
            legacy_signed_posting(expense.account_id, Decimal("12"), "CNY"),
        ],
        allow_legacy_signed=True,
    )
    service._commit_ledger_change(transaction, allow_legacy_signed_postings=True)

    reversal, replay = service.reverse_transaction(
        token,
        {"transaction_id": transaction.transaction_id, "memo": "reverse legacy"},
        idempotency_key="reverse-legacy-canonical",
    )

    assert replay is False
    assert [(posting.amount, posting.side, posting.amount_semantics) for posting in reversal.postings] == [
        (Decimal("12"), "debit", "debit_credit"),
        (Decimal("12"), "credit", "debit_credit"),
    ]

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select amount, side, amount_semantics
            from postings
            where transaction_id = ?
            order by position
            """,
            (reversal.transaction_id,),
        ).fetchall()

    assert rows == [("12", "debit", "debit_credit"), ("12", "credit", "debit_credit")]


def test_repository_save_rejects_new_legacy_signed_postings_without_explicit_compatibility(tmp_path):
    database_path = tmp_path / "legacy-write-guard.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Guard Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="legacy-write-guard-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Guard Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="legacy-write-guard-expense",
    )
    service.ledger.accounts[cash.account_id] = cash
    service.ledger.accounts[expense.account_id] = expense
    transaction = service.ledger.create_transaction(
        "legacy write guard",
        [
            legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY"),
            legacy_signed_posting(expense.account_id, Decimal("12"), "CNY"),
        ],
        allow_legacy_signed=True,
    )

    with pytest.raises(ValidationError, match="new confirmed transactions must use debit_credit semantics"):
        service._commit_ledger_change(transaction)


def test_repository_save_rejects_new_legacy_signed_draft_postings_without_explicit_compatibility(tmp_path):
    database_path = tmp_path / "legacy-draft-write-guard.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Guard Draft Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="legacy-draft-write-guard-cash",
    )
    draft = DraftTransaction(
        draft_id="draft_legacy_write_guard",
        memo="legacy draft write guard",
        state="ready_to_confirm",
        proposed_postings=[legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY")],
        missing_fields=[],
        source="agent",
        confidence=0.9,
        book_id=cash.book_id,
    )

    with pytest.raises(ValidationError, match="new draft postings must use debit_credit semantics"):
        service._commit_draft_change(draft)


def test_repository_save_rejects_unbalanced_debit_credit_draft_postings_without_builder(tmp_path):
    database_path = tmp_path / "unbalanced-draft-repository-write.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Unbalanced Draft Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="unbalanced-draft-repository-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Unbalanced Draft Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="unbalanced-draft-repository-expense",
    )
    draft = DraftTransaction(
        draft_id="draft_unbalanced_repository_write",
        memo="unbalanced draft repository write",
        state="ready_to_confirm",
        proposed_postings=[
            credit_posting(cash.account_id, Decimal("12"), "CNY"),
            debit_posting(expense.account_id, Decimal("10"), "CNY"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
        book_id=cash.book_id,
    )

    with pytest.raises(ValidationError, match="draft postings must balance by currency"):
        service._commit_draft_change(draft)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "select count(*) from drafts where draft_id = ?",
            (draft.draft_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "select count(*) from draft_postings where draft_id = ?",
            (draft.draft_id,),
        ).fetchone()[0] == 0


def test_repository_save_rejects_mixed_draft_posting_semantics_even_with_legacy_compatibility(tmp_path):
    database_path = tmp_path / "mixed-draft-repository-write.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Mixed Draft Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="mixed-draft-repository-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Mixed Draft Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="mixed-draft-repository-expense",
    )
    draft = DraftTransaction(
        draft_id="draft_mixed_repository_write",
        memo="mixed draft repository write",
        state="ready_to_confirm",
        proposed_postings=[
            legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY"),
            debit_posting(expense.account_id, Decimal("12"), "CNY"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
        book_id=cash.book_id,
    )

    with pytest.raises(ValidationError, match="draft postings must not mix legacy signed and debit/credit semantics"):
        service._commit_draft_change(draft, allow_legacy_signed_postings=True)


def test_repository_save_rejects_unbalanced_legacy_signed_draft_postings_even_with_compatibility(tmp_path):
    database_path = tmp_path / "unbalanced-legacy-draft-repository-write.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Unbalanced Legacy Draft Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="unbalanced-legacy-draft-repository-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Unbalanced Legacy Draft Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="unbalanced-legacy-draft-repository-expense",
    )
    draft = DraftTransaction(
        draft_id="draft_unbalanced_legacy_repository_write",
        memo="unbalanced legacy draft repository write",
        state="ready_to_confirm",
        proposed_postings=[
            legacy_signed_posting(cash.account_id, Decimal("-12"), "CNY"),
            legacy_signed_posting(expense.account_id, Decimal("10"), "CNY"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
        book_id=cash.book_id,
    )

    with pytest.raises(ValidationError, match="legacy signed draft postings must balance by currency"):
        service._commit_draft_change(draft, allow_legacy_signed_postings=True)


def test_repository_save_rejects_unbalanced_debit_credit_postings_without_builder(tmp_path):
    database_path = tmp_path / "unbalanced-repository-write.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Unbalanced Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="unbalanced-repository-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Unbalanced Expense", "type": "expense", "currency": "CNY"},
        idempotency_key="unbalanced-repository-expense",
    )
    transaction = Transaction(
        transaction_id="txn_unbalanced_repository_write",
        book_id=cash.book_id,
        memo="unbalanced repository write",
        occurred_at=datetime.now(timezone.utc),
        purpose="unbalanced repository write",
        postings=[
            debit_posting(cash.account_id, Decimal("10"), "CNY"),
            debit_posting(expense.account_id, Decimal("10"), "CNY"),
        ],
    )

    with pytest.raises(ValidationError, match="postings must balance by currency"):
        service._commit_ledger_change(transaction)


def test_service_startup_rejects_duplicate_balance_adjustment_postings(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {"name": "Dirty Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="dirty-cash",
    )
    transaction, _ = service.adjust_balance(
        token,
        {
            "account_id": account.account_id,
            "amount": "100",
            "currency": "CNY",
            "purpose": "seed balance",
        },
        idempotency_key="dirty-balance",
    )

    with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                """
                select transaction_id, book_id, account_id, side, amount_semantics, amount, currency
                from postings
                where transaction_id = ?
                order by position
                """,
                (transaction.transaction_id,),
            ).fetchall()
            for offset, row in enumerate(rows, start=2):
                transaction_id, book_id, account_id, side, amount_semantics, amount, currency = row
                connection.execute(
                    """
                    insert into postings (
                        transaction_id, book_id, position, account_id, side, amount_semantics, amount, currency
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (transaction_id, book_id, offset, account_id, side, amount_semantics, amount, currency),
                )

    with pytest.raises(ValidationError, match="balance adjustment transaction requires exactly two postings"):
        FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")


def test_reclassification_does_not_rewrite_confirmed_postings(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Classify Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="classify-cash",
    )
    food, _ = service.create_category(token, {"kind": "expense", "name": "Food"}, idempotency_key="classify-food")
    dining, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Dining"},
        idempotency_key="classify-dining",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "38",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": food.category_id,
            "purpose": "lunch",
        },
        idempotency_key="classify-lunch",
    )
    with sqlite3.connect(database_path) as connection:
        posting_ids_before = connection.execute(
            "select id from postings where transaction_id = ? order by position",
            (transaction.transaction_id,),
        ).fetchall()

    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.lower().split()))

    event.listen(service.storage.engine, "before_cursor_execute", capture_statement)
    try:
        service.reclassify_transaction(
            token,
            {"transaction_id": transaction.transaction_id, "category_id": dining.category_id},
            idempotency_key="classify-lunch-dining",
        )
    finally:
        event.remove(service.storage.engine, "before_cursor_execute", capture_statement)

    with sqlite3.connect(database_path) as connection:
        posting_ids_after = connection.execute(
            "select id from postings where transaction_id = ? order by position",
            (transaction.transaction_id,),
        ).fetchall()

    assert posting_ids_after == posting_ids_before
    assert not any(statement.startswith("delete from postings") for statement in statements)
