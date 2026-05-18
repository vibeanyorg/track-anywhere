from __future__ import annotations

import pytest

from track_anywhere.errors import IdempotencyConflict, PolicyDenied, ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_books_scope_accounts_to_separate_ledgers(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    default_book = service.get_book(token, "book_default")
    travel_book, replay = service.create_book(
        token,
        {"name": "Tokyo Trip", "kind": "travel", "base_currency": "JPY", "timezone": "Asia/Tokyo"},
        idempotency_key="book-tokyo-trip",
    )
    assert replay is False

    default_cash, _ = service.create_account(
        token,
        {"name": "Default Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="book-default-cash",
    )
    travel_cash, _ = service.create_book_account(
        token,
        travel_book.book_id,
        {"name": "Tokyo Cash", "type": "asset", "currency": "JPY"},
        idempotency_key="book-travel-cash",
    )

    assert default_cash.book_id == default_book.book_id
    assert travel_cash.book_id == travel_book.book_id
    assert [account.account_id for account in service.list_book_accounts(token, travel_book.book_id)] == [travel_cash.account_id]
    assert [account.account_id for account in service.list_accounts(token)] == [default_cash.account_id]

    agent, _ = service.issue_agent_credential_command(
        token,
        {"scopes": ["account:read"]},
        idempotency_key="book-agent-account-read",
    )
    with pytest.raises(PolicyDenied):
        service.list_book_accounts(agent["token"], travel_book.book_id)


def test_book_scoped_transaction_rejects_accounts_from_another_book(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    travel_book, _ = service.create_book(
        token,
        {"name": "Travel", "kind": "travel", "base_currency": "CNY"},
        idempotency_key="book-scope-travel",
    )
    cash, _ = service.create_account(
        token,
        {"name": "Default Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="book-scope-cash",
    )
    food, _ = service.create_account(
        token,
        {"name": "Default Food", "type": "expense", "currency": "CNY"},
        idempotency_key="book-scope-food",
    )

    with pytest.raises(ValidationError):
        service.record_book_transaction(
            token,
            travel_book.book_id,
            {
                "amount": "10",
                "currency": "CNY",
                "from_account_id": cash.account_id,
                "to_account_id": food.account_id,
                "purpose": "wrong book",
            },
            idempotency_key="book-scope-bad-transaction",
        )


def test_category_tree_versions_preserve_line_snapshot_after_rename(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Cash", "type": "asset", "currency": "CNY", "opening_balance": "200"},
        idempotency_key="snapshot-cash",
    )
    delivery, _ = service.create_category(
        token,
        {"kind": "expense", "primary": "餐饮", "secondary": "外卖"},
        idempotency_key="snapshot-delivery",
    )
    parent = next(
        category
        for category in service.list_categories(token, kind="expense", primary="餐饮")
        if category.secondary is None
    )
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "32",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": delivery.category_id,
            "purpose": "delivery",
        },
        idempotency_key="snapshot-expense",
    )

    service.update_book_category(
        token,
        parent.book_id,
        parent.category_id,
        {"name": "餐饮消费"},
        idempotency_key="rename-food-parent",
    )

    assert service.get_category(token, delivery.category_id).path_cache == "餐饮消费 / 外卖"
    assert transaction.lines[0].category_path_snapshot["path"] == "餐饮 / 外卖"
    assert any(event.event_type == "rename" for event in service.list_book_classification_events(token, parent.book_id))


def test_budget_targets_and_spending_report_use_lines(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    book = service.get_book(token, "book_default")
    cash, _ = service.create_account(
        token,
        {"name": "Budget Cash", "type": "asset", "currency": "CNY", "opening_balance": "500"},
        idempotency_key="budget-cash",
    )
    lunch, _ = service.create_category(
        token,
        {"kind": "expense", "primary": "餐饮", "secondary": "午餐"},
        idempotency_key="budget-lunch",
    )
    parent = next(category for category in service.list_categories(token, kind="expense", primary="餐饮") if category.level == 1)
    budget, _ = service.create_budget(
        token,
        book.book_id,
        {"name": "餐饮月度", "period": "monthly", "currency": "CNY", "total_amount": "1000"},
        idempotency_key="budget-food",
    )
    target, _ = service.add_budget_target(
        token,
        book.book_id,
        budget.budget_id,
        {"target_type": "category_subtree", "target_id": parent.category_id, "amount": "1000"},
        idempotency_key="budget-food-target",
    )
    service.record_expense(
        token,
        {"amount": "45", "currency": "CNY", "from_account_id": cash.account_id, "category_id": lunch.category_id, "purpose": "lunch"},
        idempotency_key="budget-lunch-expense",
    )

    report = service.spending_report(token, book.book_id, group_by="category_parent", currency="CNY")
    execution = service.budget_execution_report(token, book.book_id, budget.budget_id)

    assert target.target_id == parent.category_id
    assert report["groups"] == [{"key": "餐饮", "currency": "CNY", "amount": "45", "line_count": 1}]
    assert execution["spent"] == "45"
    assert execution["remaining"] == "955"


def test_budget_execution_supports_project_and_merchant_targets(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    book = service.get_book(token, "book_default")
    cash, _ = service.create_account(
        token,
        {"name": "Dimension Cash", "type": "asset", "currency": "CNY", "opening_balance": "500"},
        idempotency_key="dimension-cash",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "primary": "交通", "secondary": "打车"},
        idempotency_key="dimension-taxi",
    )
    budget, _ = service.create_budget(
        token,
        book.book_id,
        {"name": "项目商户预算", "period": "monthly", "currency": "CNY", "total_amount": "300"},
        idempotency_key="dimension-budget",
    )
    project_target, _ = service.add_budget_target(
        token,
        book.book_id,
        budget.budget_id,
        {"target_type": "project", "target_id": "project_tokyo"},
        idempotency_key="dimension-project-target",
    )
    merchant_target, _ = service.add_budget_target(
        token,
        book.book_id,
        budget.budget_id,
        {"target_type": "merchant", "target_id": "merchant_didi"},
        idempotency_key="dimension-merchant-target",
    )
    transaction, _ = service.record_expense(
        token,
        {"amount": "80", "currency": "CNY", "from_account_id": cash.account_id, "category_id": category.category_id, "purpose": "taxi"},
        idempotency_key="dimension-taxi-expense",
    )
    transaction.lines[0].project_id = "project_tokyo"
    transaction.lines[0].merchant_id = "merchant_didi"

    execution = service.budget_execution_report(token, book.book_id, budget.budget_id)

    assert {target["budget_target_id"]: target["amount"] for target in execution["targets"]} == {
        project_target.budget_target_id: "80",
        merchant_target.budget_target_id: "80",
    }
    assert execution["spent"] == "160"


def test_book_scoped_recurring_generates_drafts_in_book(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    travel_book, _ = service.create_book(
        token,
        {"name": "Travel Recurring", "kind": "travel", "base_currency": "CNY"},
        idempotency_key="recurring-travel-book",
    )
    cash, _ = service.create_book_account(
        token,
        travel_book.book_id,
        {"name": "Travel Cash", "type": "asset", "currency": "CNY", "opening_balance": "300"},
        idempotency_key="recurring-travel-cash",
    )
    category, _ = service.create_book_category(
        token,
        travel_book.book_id,
        {"kind": "expense", "primary": "交通", "secondary": "地铁"},
        idempotency_key="recurring-travel-category",
    )
    item, _ = service.create_recurring_item(
        token,
        {
            "book_id": travel_book.book_id,
            "name": "Metro pass",
            "kind": "paid",
            "amount": "30",
            "currency": "CNY",
            "recurrence": {"type": "monthly_day", "day": 16},
            "reminder_days": [1],
            "anchor_date": "2026-05-16",
            "source_account_id": cash.account_id,
            "category_id": category.category_id,
        },
        idempotency_key="recurring-travel-item",
    )

    default_result, _ = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="recurring-default-drafts",
    )
    travel_result, _ = service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="recurring-travel-drafts",
        book_id=travel_book.book_id,
    )

    assert item.book_id == travel_book.book_id
    assert default_result["created"] == []
    assert len(travel_result["created"]) == 1
    draft = service.drafts.drafts[travel_result["created"][0]["draft_id"]]
    assert draft.book_id == travel_book.book_id


def test_recurring_draft_idempotency_is_scoped_by_book(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    travel_book, _ = service.create_book(
        token,
        {"name": "Scoped Recurring", "kind": "travel", "base_currency": "CNY"},
        idempotency_key="scoped-recurring-book",
    )

    service.generate_recurring_drafts(
        token,
        {"as_of": "2026-06-16"},
        idempotency_key="same-recurring-draft-key",
    )

    with pytest.raises(IdempotencyConflict):
        service.generate_recurring_drafts(
            token,
            {"as_of": "2026-06-16"},
            idempotency_key="same-recurring-draft-key",
            book_id=travel_book.book_id,
        )
