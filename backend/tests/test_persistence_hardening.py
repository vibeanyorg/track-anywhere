from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from sqlalchemy import event

from track_anywhere.errors import ValidationError
from track_anywhere.ledger import Posting
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


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
            Posting(account.account_id, Decimal("100"), "CNY"),
            Posting(adjustment_account_id, Decimal("-100"), "CNY"),
        ]
    )

    with pytest.raises(ValidationError, match="confirmed transaction postings are immutable"):
        service._persist_ledger_change(transaction)

    with sqlite3.connect(database_path) as connection:
        posting_count = connection.execute(
            "select count(*) from postings where transaction_id = ?",
            (transaction.transaction_id,),
        ).fetchone()[0]

    assert posting_count == 2


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
            select transaction_id, book_id, account_id, amount, currency
            from postings
            where transaction_id = ?
            order by position
            """,
            (transaction.transaction_id,),
        ).fetchall()
        for offset, row in enumerate(rows, start=2):
            transaction_id, book_id, account_id, amount, currency = row
            connection.execute(
                """
                insert into postings (transaction_id, book_id, position, account_id, amount, currency)
                values (?, ?, ?, ?, ?, ?)
                """,
                (transaction_id, book_id, offset, account_id, amount, currency),
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
