from __future__ import annotations

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_book_category_and_budget_reports_use_storage_truth_when_memory_mirror_is_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    book = service.get_book(token, "book_default")
    cash, _ = service.create_account(
        token,
        {"name": "Report Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="report-truth-cash",
    )
    food, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Report Truth Food"},
        idempotency_key="report-truth-food",
    )
    lunch, _ = service.create_category(
        token,
        {"kind": "expense", "name": "Report Truth Lunch", "parent_id": food.category_id},
        idempotency_key="report-truth-lunch",
    )
    budget, _ = service.create_budget(
        token,
        book.book_id,
        {"name": "Report Truth Budget", "period": "monthly", "currency": "CNY", "total_amount": "100"},
        idempotency_key="report-truth-budget",
    )
    service.add_budget_target(
        token,
        book.book_id,
        budget.budget_id,
        {"target_type": "category_subtree", "target_id": food.category_id},
        idempotency_key="report-truth-budget-target",
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "12",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": lunch.category_id,
            "purpose": "storage truth report lunch",
        },
        idempotency_key="report-truth-expense",
    )
    service.ledger.transactions.clear()

    book_transactions = service.list_book_transactions(token, book.book_id)
    category_summary = service.category_summary(token, kind="expense", currency="CNY")
    spending = service.spending_report(token, book.book_id, group_by="category_parent", currency="CNY")
    execution = service.budget_execution_report(token, book.book_id, budget.budget_id)

    assert transaction.transaction_id in {item.transaction_id for item in book_transactions}
    assert category_summary["groups"][0]["amount"] == "12"
    assert spending["groups"] == [{"key": "Report Truth Food", "currency": "CNY", "amount": "12", "line_count": 1}]
    assert execution["spent"] == "12"
    assert execution["remaining"] == "88"
