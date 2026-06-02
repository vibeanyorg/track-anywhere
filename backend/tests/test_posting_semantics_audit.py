from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from track_anywhere.drafts import DraftTransaction
from track_anywhere.errors import ValidationError
from track_anywhere.ledger import Account, Transaction, credit_posting, debit_posting, legacy_signed_posting
from track_anywhere.posting_semantics import (
    DEBIT_CREDIT_AMOUNT_RULE,
    DEBIT_CREDIT_SIDE_RULE,
    LEGACY_SIGNED_SCOPE,
    POSTING_CANONICAL_MODEL,
)
from track_anywhere.posting_semantics_audit import audit_posting_semantics, posting_semantics_cutover_plan
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_audit_flags_legacy_credit_card_postings_before_positive_only_cutover():
    card = Account(
        account_id="acc_card",
        name="Visa",
        type="liability",
        subtype="credit_card",
        currency="USD",
    )
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_legacy_card_expense",
        memo="legacy card expense",
        occurred_at=datetime.now(timezone.utc),
        purpose="legacy card expense",
        postings=[
            legacy_signed_posting(card.account_id, Decimal("-11.08"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("11.08"), "USD"),
        ],
    )

    audit = audit_posting_semantics([transaction], {card.account_id: card, expense.account_id: expense})

    assert audit["cutover_ready"] is False
    assert audit["auto_rewrite_ready"] is False
    assert audit["counts"]["legacy_signed_postings"] == 2
    assert audit["counts"]["auto_rewrite_candidates"] == 1
    assert audit["counts"]["manual_review_blockers"] == 1
    assert audit["counts"]["credit_card_legacy_postings_requiring_review"] == 1
    assert audit["issues"] == [
        {
            "issue_type": "legacy_liability_review_required",
            "transaction_id": "txn_legacy_card_expense",
            "position": 0,
            "account_id": "acc_card",
            "account_type": "liability",
            "account_subtype": "credit_card",
            "currency": "USD",
            "legacy_amount": "-11.08",
            "inferred_side": "credit",
            "inferred_positive_amount": "11.08",
            "legacy_balance_delta": "-11.08",
            "debit_credit_balance_delta": "11.08",
            "amount_semantics": "legacy_signed",
            "severity": "warning",
            "reason": "legacy liability posting changes meaning under natural debit/credit balance semantics and requires economic review",
        }
    ]
    assert audit["manual_review_blockers"] == audit["issues"]
    assert audit["manual_review_recommendations"] == [
        {
            "transaction_id": "txn_legacy_card_expense",
            "position": 0,
            "account_id": "acc_card",
            "account_subtype": "credit_card",
            "currency": "USD",
            "legacy_amount": "-11.08",
            "amount_semantics": "legacy_signed",
            "inferred_side_from_legacy_sign": "credit",
            "inferred_positive_amount": "11.08",
            "recommended_action": "manual_review_required_credit_card_semantics",
            "recommendation_reason": "credit-card signs were ambiguous under legacy signed postings; choose whether this row represents a charge/outstanding liability or a payment/overpayment",
            "resolution_options": [
                {
                    "action": "confirm_as_outstanding_liability",
                    "target_side": "credit",
                    "target_amount": "11.08",
                    "meaning": "liability balance increases by this amount",
                },
                {
                    "action": "confirm_as_liability_reduction_or_overpayment",
                    "target_side": "debit",
                    "target_amount": "11.08",
                    "meaning": "liability balance decreases by this amount",
                },
            ],
        }
    ]
    assert audit["auto_rewrite_candidates"] == [
        {
            "transaction_id": "txn_legacy_card_expense",
            "position": 1,
            "account_id": "acc_expense",
            "account_type": "expense",
            "account_subtype": None,
            "currency": "USD",
            "legacy_amount": "11.08",
            "amount_semantics": "legacy_signed",
            "target_side": "debit",
            "target_amount": "11.08",
            "legacy_balance_delta": "11.08",
            "target_balance_delta": "11.08",
        }
    ]


def test_audit_accepts_debit_credit_positive_postings():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_new_expense",
        memo="new expense",
        occurred_at=datetime.now(timezone.utc),
        purpose="new expense",
        postings=[
            credit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )

    audit = audit_posting_semantics([transaction], {cash.account_id: cash, expense.account_id: expense})

    assert audit["cutover_ready"] is True
    assert audit["auto_rewrite_ready"] is True
    assert audit["counts"]["legacy_signed_postings"] == 0
    assert audit["counts"]["debit_credit_postings"] == 2
    assert audit["issues"] == []
    assert audit["auto_rewrite_candidates"] == []


def test_audit_blocks_unbalanced_debit_credit_transactions():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_unbalanced_debit_credit",
        memo="bad debit credit",
        occurred_at=datetime.now(timezone.utc),
        purpose="bad debit credit",
        postings=[
            debit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )

    audit = audit_posting_semantics([transaction], {cash.account_id: cash, expense.account_id: expense})

    assert audit["cutover_ready"] is False
    assert audit["auto_rewrite_ready"] is False
    assert audit["counts"]["unbalanced_transactions"] == 1
    assert audit["manual_review_blockers"][0]["issue_type"] == "unbalanced_debit_credit_transaction"
    assert audit["manual_review_blockers"][0]["reason"] == "debit/credit transaction postings do not balance by currency"


def test_cutover_plan_blocks_unbalanced_debit_credit_drafts():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    draft = DraftTransaction(
        draft_id="draft_unbalanced_debit_credit",
        memo="bad debit credit draft",
        state="ready_to_confirm",
        proposed_postings=[
            credit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("4"), "USD"),
        ],
        missing_fields=[],
        source="agent",
        confidence=0.9,
    )

    plan = posting_semantics_cutover_plan(
        [],
        {cash.account_id: cash, expense.account_id: expense},
        drafts=[draft],
    )

    assert plan["cutover_ready"] is False
    assert plan["auto_rewrite_ready"] is False
    assert plan["counts"]["unbalanced_transactions"] == 1
    assert plan["manual_review_blockers"][0]["transaction_id"] == "draft:draft_unbalanced_debit_credit"
    assert plan["manual_review_blockers"][0]["issue_type"] == "unbalanced_debit_credit_transaction"
    assert plan["manual_review_blockers"][0]["amount_semantics"] == "debit_credit"


def test_audit_blocks_invalid_debit_credit_side():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_invalid_debit_credit_side",
        memo="bad side",
        occurred_at=datetime.now(timezone.utc),
        purpose="bad side",
        postings=[
            credit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )
    transaction.postings[0].side = "increase"  # type: ignore[assignment]

    audit = audit_posting_semantics([transaction], {cash.account_id: cash, expense.account_id: expense})

    assert audit["cutover_ready"] is False
    assert audit["auto_rewrite_ready"] is False
    assert audit["counts"]["invalid_debit_credit_postings"] == 1
    assert audit["manual_review_blockers"][0]["issue_type"] == "invalid_debit_credit_shape"
    assert audit["manual_review_blockers"][0]["position"] == 0
    assert audit["manual_review_blockers"][0]["amount_semantics"] == "debit_credit"
    assert audit["manual_review_blockers"][0]["reason"] == "debit_credit posting must have debit/credit side and positive amount"


def test_audit_blocks_mixed_posting_semantics_transactions():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_mixed_semantics",
        memo="mixed semantics",
        occurred_at=datetime.now(timezone.utc),
        purpose="mixed semantics",
        postings=[
            legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )

    audit = audit_posting_semantics([transaction], {cash.account_id: cash, expense.account_id: expense})

    assert audit["cutover_ready"] is False
    assert audit["auto_rewrite_ready"] is False
    assert audit["counts"]["mixed_semantics_transactions"] == 1
    assert any(
        issue["issue_type"] == "mixed_transaction_semantics"
        and issue["reason"] == "transaction postings must not mix legacy signed and debit/credit semantics"
        for issue in audit["manual_review_blockers"]
    )


def test_audit_reports_invalid_legacy_zero_as_manual_blocker():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_zero_legacy",
        memo="bad legacy zero",
        occurred_at=datetime.now(timezone.utc),
        purpose="bad legacy zero",
        postings=[
            legacy_signed_posting(cash.account_id, Decimal("0"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )

    audit = audit_posting_semantics([transaction], {cash.account_id: cash, expense.account_id: expense})

    assert audit["cutover_ready"] is False
    assert audit["auto_rewrite_ready"] is False
    assert audit["counts"]["legacy_signed_postings"] == 2
    assert audit["counts"]["manual_review_blockers"] == 2
    assert audit["manual_review_blockers"][0]["reason"] == "legacy signed posting amount must not be zero"
    assert audit["manual_review_blockers"][1]["issue_type"] == "unbalanced_legacy_signed_transaction"


def test_cutover_plan_auto_rewrites_income_to_positive_credit_normal_balance():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    income = Account(account_id="acc_income", name="Income", type="income", currency="USD")
    transaction = Transaction(
        transaction_id="txn_legacy_income",
        memo="legacy income",
        occurred_at=datetime.now(timezone.utc),
        purpose="legacy income",
        postings=[
            legacy_signed_posting(cash.account_id, Decimal("100"), "USD"),
            legacy_signed_posting(income.account_id, Decimal("-100"), "USD"),
        ],
    )

    plan = posting_semantics_cutover_plan([transaction], {cash.account_id: cash, income.account_id: income})

    assert plan["auto_rewrite_ready"] is True
    income_candidate = next(
        candidate for candidate in plan["auto_rewrite_candidates"] if candidate["account_id"] == income.account_id
    )
    assert income_candidate["amount_semantics"] == "legacy_signed"
    assert income_candidate["target_side"] == "credit"
    assert income_candidate["target_amount"] == "100"
    assert income_candidate["legacy_balance_delta"] == "-100"
    assert income_candidate["target_balance_delta"] == "100"


def test_cutover_plan_allows_auto_rewrite_when_only_non_liability_legacy_rows_remain():
    cash = Account(account_id="acc_cash", name="Cash", type="asset", currency="USD")
    expense = Account(account_id="acc_expense", name="Expense", type="expense", currency="USD")
    transaction = Transaction(
        transaction_id="txn_legacy_expense",
        memo="legacy expense",
        occurred_at=datetime.now(timezone.utc),
        purpose="legacy expense",
        postings=[
            legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )

    plan = posting_semantics_cutover_plan([transaction], {cash.account_id: cash, expense.account_id: expense})

    assert plan["cutover_ready"] is False
    assert plan["auto_rewrite_ready"] is True
    assert plan["positive_only_rewrite_allowed"] is True
    assert plan["next_action"] == "auto_rewrite_legacy_signed_postings"
    assert len(plan["auto_rewrite_candidates"]) == 2
    assert plan["manual_review_blockers"] == []


def test_service_posting_semantics_audit_reports_new_writes_as_cutover_ready():
    service = FinanceService()
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Cash", "type": "asset", "currency": "USD", "opening_balance": "20"},
        idempotency_key="audit-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Food"},
        idempotency_key="audit-food",
    )
    service.record_expense(
        token,
        {
            "from_account_id": cash.account_id,
            "amount": "5",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "lunch",
        },
        idempotency_key="audit-expense",
    )

    audit = service.posting_semantics_audit(token)

    assert audit["cutover_ready"] is True
    assert audit["auto_rewrite_ready"] is True
    assert audit["counts"]["legacy_signed_postings"] == 0
    assert audit["counts"]["debit_credit_postings"] == 4
    assert audit["issues"] == []


def test_service_rewrite_posting_semantics_rewrites_auto_candidates(tmp_path):
    database_path = tmp_path / "rewrite-posting-semantics.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Rewrite Cash", "type": "asset", "currency": "USD"},
        idempotency_key="rewrite-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Rewrite Food"},
        idempotency_key="rewrite-food",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "from_account_id": cash.account_id,
            "amount": "5",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "legacy rewrite fixture",
        },
        idempotency_key="rewrite-expense",
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            update postings
            set amount = case when account_id = ? then '-5' else '+5' end,
                side = case when account_id = ? then 'credit' else 'debit' end,
                amount_semantics = 'legacy_signed'
            where transaction_id = ?
            """,
            (cash.account_id, cash.account_id, transaction.transaction_id),
        )

    before = service.posting_semantics_cutover_plan(token)
    result = service.rewrite_posting_semantics(token, idempotency_key="rewrite-posting-semantics")
    after = result["plan_after"]

    assert before["next_action"] == "auto_rewrite_legacy_signed_postings"
    assert result["status"] == "rewritten"
    assert result["idempotent_replay"] is False
    assert result["posting_semantics"]["canonical_model"] == POSTING_CANONICAL_MODEL
    assert result["posting_semantics"]["debit_credit_amount_rule"] == DEBIT_CREDIT_AMOUNT_RULE
    assert result["posting_semantics"]["debit_credit_side_rule"] == DEBIT_CREDIT_SIDE_RULE
    assert result["posting_semantics"]["legacy_signed_scope"] == LEGACY_SIGNED_SCOPE
    assert result["confirmed_postings_rewritten"] == 2
    replay_result = service.rewrite_posting_semantics(token, idempotency_key="rewrite-posting-semantics")
    assert replay_result["idempotent_replay"] is True
    assert replay_result["status"] == "rewritten"
    assert replay_result["confirmed_postings_rewritten"] == 2
    assert after["cutover_ready"] is True
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select account_id, amount, side, amount_semantics
            from postings
            where transaction_id = ?
            order by position
            """,
            (transaction.transaction_id,),
        ).fetchall()
    assert rows == [
        (cash.account_id, "5", "credit", "debit_credit"),
        (next(posting.account_id for posting in transaction.postings if posting.account_id != cash.account_id), "5", "debit", "debit_credit"),
    ]


def test_service_rewrite_posting_semantics_fails_if_cutover_not_reached(tmp_path, monkeypatch):
    database_path = tmp_path / "rewrite-posting-semantics-noop.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Noop Rewrite Cash", "type": "asset", "currency": "USD"},
        idempotency_key="noop-rewrite-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Noop Rewrite Food"},
        idempotency_key="noop-rewrite-food",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "from_account_id": cash.account_id,
            "amount": "5",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "legacy rewrite noop fixture",
        },
        idempotency_key="noop-rewrite-expense",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            update postings
            set amount = case when account_id = ? then '-5' else '+5' end,
                amount_semantics = 'legacy_signed'
            where transaction_id = ?
            """,
            (cash.account_id, transaction.transaction_id),
        )

    monkeypatch.setattr(
        service.storage,
        "rewrite_legacy_postings_to_debit_credit",
        lambda *, book_id, candidates: {"confirmed_postings_rewritten": 0, "draft_postings_rewritten": 0},
    )

    with pytest.raises(ValidationError, match="did not reach cutover_ready"):
        service.rewrite_posting_semantics(token, idempotency_key="noop-rewrite-posting-semantics")


def test_service_rewrite_posting_semantics_uses_candidate_position_for_duplicate_legacy_rows(tmp_path):
    database_path = tmp_path / "rewrite-duplicate-posting-semantics.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Duplicate Cash", "type": "asset", "currency": "USD"},
        idempotency_key="rewrite-duplicate-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Duplicate Expense", "type": "expense", "currency": "USD"},
        idempotency_key="rewrite-duplicate-expense",
    )
    service.ledger.accounts[cash.account_id] = cash
    service.ledger.accounts[expense.account_id] = expense
    transaction = service.ledger.create_transaction(
        "duplicate legacy rows",
        [
            legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
            legacy_signed_posting(cash.account_id, Decimal("-5"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
        ],
        allow_legacy_signed=True,
    )
    service._commit_ledger_change(transaction, allow_legacy_signed_postings=True)

    before = service.posting_semantics_cutover_plan(token)
    result = service.rewrite_posting_semantics(token, idempotency_key="rewrite-duplicate-posting-semantics")

    assert [candidate["position"] for candidate in before["auto_rewrite_candidates"]] == [0, 1, 2, 3]
    assert result["confirmed_postings_rewritten"] == 4
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select position, account_id, amount, side, amount_semantics
            from postings
            where transaction_id = ?
            order by position
            """,
            (transaction.transaction_id,),
        ).fetchall()
    assert rows == [
        (0, cash.account_id, "5", "credit", "debit_credit"),
        (1, cash.account_id, "5", "credit", "debit_credit"),
        (2, expense.account_id, "5", "debit", "debit_credit"),
        (3, expense.account_id, "5", "debit", "debit_credit"),
    ]


def test_service_rewrite_posting_semantics_rewrites_draft_auto_candidates(tmp_path):
    database_path = tmp_path / "rewrite-draft-posting-semantics.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Draft Cash", "type": "asset", "currency": "USD"},
        idempotency_key="rewrite-draft-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Draft Expense", "type": "expense", "currency": "USD"},
        idempotency_key="rewrite-draft-expense",
    )
    draft, _ = service.capture_draft(
        token,
        {
            "memo": "draft rewrite fixture",
            "amount": "7",
            "currency": "USD",
            "source_account_id": cash.account_id,
            "expense_account_id": expense.account_id,
        },
        idempotency_key="rewrite-draft-capture",
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            update draft_postings
            set amount = case when account_id = ? then '-7' else '+7' end,
                side = case when account_id = ? then 'credit' else 'debit' end,
                amount_semantics = 'legacy_signed'
            where draft_id = ?
            """,
            (cash.account_id, cash.account_id, draft.draft_id),
        )

    before = service.posting_semantics_cutover_plan(token)
    result = service.rewrite_posting_semantics(token, idempotency_key="rewrite-draft-posting-semantics")
    after = result["plan_after"]

    assert before["next_action"] == "auto_rewrite_legacy_signed_postings"
    assert before["counts"]["legacy_signed_draft_postings"] == 2
    assert result["idempotent_replay"] is False
    assert result["draft_postings_rewritten"] == 2
    assert after["cutover_ready"] is True
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select account_id, amount, side, amount_semantics
            from draft_postings
            where draft_id = ?
            order by position
            """,
            (draft.draft_id,),
        ).fetchall()
    assert rows == [
        (cash.account_id, "7", "credit", "debit_credit"),
        (expense.account_id, "7", "debit", "debit_credit"),
    ]


def test_service_resolves_manual_credit_card_review_decision(tmp_path):
    database_path = tmp_path / "resolve-credit-card-review.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {"name": "Review Visa", "type": "liability", "subtype": "credit_card", "currency": "USD"},
        idempotency_key="review-card",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Review Domains"},
        idempotency_key="review-domain-category",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "from_account_id": card.account_id,
            "amount": "11.08",
            "currency": "USD",
            "category_id": category.category_id,
            "purpose": "legacy review fixture",
        },
        idempotency_key="review-card-expense",
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            update postings
            set amount = '-11.08',
                side = 'credit',
                amount_semantics = 'legacy_signed'
            where transaction_id = ?
              and account_id = ?
            """,
            (transaction.transaction_id, card.account_id),
        )

    before = service.posting_semantics_cutover_plan(token)
    result = service.resolve_posting_semantics_reviews(
        token,
        {
            "decisions": [
                {
                    "transaction_id": transaction.transaction_id,
                    "position": 0,
                    "account_id": card.account_id,
                    "currency": "USD",
                    "legacy_amount": "-11.08",
                    "action": "confirm_as_outstanding_liability",
                }
            ]
        },
        idempotency_key="resolve-card-review",
    )

    assert before["next_action"] == "manual_review_required"
    assert before["manual_review_recommendations"][0]["account_id"] == card.account_id
    assert before["manual_review_recommendations"][0]["amount_semantics"] == "legacy_signed"
    assert result["idempotent_replay"] is False
    assert result["posting_semantics"]["debit_credit_amount_rule"] == DEBIT_CREDIT_AMOUNT_RULE
    assert result["posting_semantics"]["debit_credit_side_rule"] == DEBIT_CREDIT_SIDE_RULE
    assert (
        result["posting_semantics"]["liability_review_actions"]["confirm_as_outstanding_liability"]["target_side"]
        == "credit"
    )
    assert result["confirmed_liability_postings_rewritten"] == 1
    assert result["plan_after"]["cutover_ready"] is True
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            select amount, side, amount_semantics
            from postings
            where transaction_id = ?
              and account_id = ?
            """,
            (transaction.transaction_id, card.account_id),
        ).fetchone()
    assert row == ("11.08", "credit", "debit_credit")


def test_service_resolves_duplicate_manual_credit_card_reviews_by_position(tmp_path):
    database_path = tmp_path / "resolve-duplicate-credit-card-review.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {"name": "Duplicate Review Visa", "type": "liability", "subtype": "credit_card", "currency": "USD"},
        idempotency_key="duplicate-review-card",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Duplicate Review Expense", "type": "expense", "currency": "USD"},
        idempotency_key="duplicate-review-expense",
    )
    service.ledger.accounts[card.account_id] = card
    service.ledger.accounts[expense.account_id] = expense
    transaction = service.ledger.create_transaction(
        "duplicate legacy card rows",
        [
            legacy_signed_posting(card.account_id, Decimal("-5"), "USD"),
            legacy_signed_posting(card.account_id, Decimal("-5"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
            legacy_signed_posting(expense.account_id, Decimal("5"), "USD"),
        ],
        allow_legacy_signed=True,
    )
    service._commit_ledger_change(transaction, allow_legacy_signed_postings=True)

    before = service.posting_semantics_cutover_plan(token)
    result = service.resolve_posting_semantics_reviews(
        token,
        {
            "decisions": [
                {
                    "transaction_id": transaction.transaction_id,
                    "position": 0,
                    "account_id": card.account_id,
                    "currency": "USD",
                    "legacy_amount": "-5",
                    "action": "confirm_as_outstanding_liability",
                },
                {
                    "transaction_id": transaction.transaction_id,
                    "position": 1,
                    "account_id": card.account_id,
                    "currency": "USD",
                    "legacy_amount": "-5",
                    "action": "confirm_as_outstanding_liability",
                },
            ]
        },
        idempotency_key="resolve-duplicate-card-review",
    )

    assert [item["position"] for item in before["manual_review_recommendations"]] == [0, 1]
    assert {item["amount_semantics"] for item in before["manual_review_recommendations"]} == {"legacy_signed"}
    assert result["confirmed_liability_postings_rewritten"] == 2
    assert result["confirmed_postings_rewritten"] == 2
    assert result["plan_after"]["cutover_ready"] is True
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select position, account_id, amount, side, amount_semantics
            from postings
            where transaction_id = ?
            order by position
            """,
            (transaction.transaction_id,),
        ).fetchall()
    assert rows == [
        (0, card.account_id, "5", "credit", "debit_credit"),
        (1, card.account_id, "5", "credit", "debit_credit"),
        (2, expense.account_id, "5", "debit", "debit_credit"),
        (3, expense.account_id, "5", "debit", "debit_credit"),
    ]
