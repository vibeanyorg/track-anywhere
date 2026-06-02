from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from track_anywhere.ledger import Posting, Transaction, credit_posting, debit_posting
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_category_line_generation_uses_debit_credit_side_for_expense():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Category Line Cash", "type": "asset", "currency": "USD"},
        idempotency_key="category-line-cash",
    )
    expense, _ = service.create_account(
        token,
        {"name": "Category Line Expense", "type": "expense", "currency": "USD"},
        idempotency_key="category-line-expense",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Category Line Food"},
        idempotency_key="category-line-food",
    )
    transaction = Transaction(
        transaction_id="txn_category_line",
        memo="category line",
        occurred_at=datetime.now(timezone.utc),
        purpose="category line",
        postings=[
            credit_posting(cash.account_id, Decimal("5"), "USD"),
            debit_posting(expense.account_id, Decimal("5"), "USD"),
        ],
    )

    service._add_category_line_for_transaction(transaction, category, accounts=(cash, expense))

    assert [(line.line_type, line.amount, line.currency, line.category_id) for line in transaction.lines] == [
        ("expense", Decimal("5"), "USD", category.category_id)
    ]


def test_category_line_generation_uses_debit_credit_side_for_income():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Category Line Income Cash", "type": "asset", "currency": "USD"},
        idempotency_key="category-line-income-cash",
    )
    income, _ = service.create_account(
        token,
        {"name": "Category Line Income", "type": "income", "currency": "USD"},
        idempotency_key="category-line-income",
    )
    category, _ = service.create_category(
        token,
        {"kind": "income", "name": "Category Line Salary"},
        idempotency_key="category-line-salary",
    )
    transaction = Transaction(
        transaction_id="txn_category_line_income",
        memo="category line income",
        occurred_at=datetime.now(timezone.utc),
        purpose="category line income",
        postings=[
            credit_posting(income.account_id, Decimal("8"), "USD"),
            debit_posting(cash.account_id, Decimal("8"), "USD"),
        ],
    )

    service._add_category_line_for_transaction(transaction, category, accounts=(cash, income))

    assert [(line.line_type, line.amount, line.currency, line.category_id) for line in transaction.lines] == [
        ("income", Decimal("8"), "USD", category.category_id)
    ]


def test_category_line_generation_rejects_signed_debit_credit_amounts_instead_of_inferencing_sign():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    expense, _ = service.create_account(
        token,
        {"name": "Signed Debit Credit Expense", "type": "expense", "currency": "USD"},
        idempotency_key="signed-debit-credit-expense",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Signed Debit Credit Food"},
        idempotency_key="signed-debit-credit-food",
    )
    transaction = Transaction(
        transaction_id="txn_signed_debit_credit_line",
        memo="signed debit credit",
        occurred_at=datetime.now(timezone.utc),
        purpose="signed debit credit",
        postings=[
            Posting(
                expense.account_id,
                Decimal("-5"),
                "USD",
                side="debit",
                amount_semantics="debit_credit",
            )
        ],
    )

    service._add_category_line_for_transaction(transaction, category, accounts=(expense,))

    assert transaction.lines == []


def test_category_line_generation_skips_unknown_posting_semantics():
    service = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = service.owner_token
    expense, _ = service.create_account(
        token,
        {"name": "Unknown Semantics Expense", "type": "expense", "currency": "USD"},
        idempotency_key="unknown-semantics-expense",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Unknown Semantics Food"},
        idempotency_key="unknown-semantics-food",
    )
    transaction = Transaction(
        transaction_id="txn_unknown_semantics_line",
        memo="unknown semantics",
        occurred_at=datetime.now(timezone.utc),
        purpose="unknown semantics",
        postings=[
            Posting(
                expense.account_id,
                Decimal("5"),
                "USD",
                side="debit",
                amount_semantics="unknown",  # type: ignore[arg-type]
            )
        ],
    )

    service._add_category_line_for_transaction(transaction, category, accounts=(expense,))

    assert transaction.lines == []
