from __future__ import annotations

import pytest

from track_anywhere.errors import ValidationError
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_categories_start_empty_and_support_two_levels(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")

    assert service.list_categories(service.owner_token) == []

    category, replay = service.create_category(
        service.owner_token,
        {"kind": "expense", "primary": "餐饮", "secondary": "外卖"},
        idempotency_key="cat-food-delivery",
    )

    assert replay is False
    assert category.kind == "expense"
    assert category.primary == "餐饮"
    assert category.secondary == "外卖"
    assert service.get_category(service.owner_token, category.category_id) == category
    category_tree = service.list_categories(service.owner_token, kind="expense", primary="餐饮")
    parent = next(item for item in category_tree if item.secondary is None)

    assert category_tree == [parent, category]
    assert parent.level == 1
    assert category.level == 2
    assert category.parent_id == parent.category_id
    assert category.path_cache == "餐饮 / 外卖"


def test_duplicate_categories_are_rejected(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    payload = {"kind": "income", "primary": "工资", "secondary": "主业"}
    service.create_category(service.owner_token, payload, idempotency_key="cat-salary-main")

    with pytest.raises(ValidationError):
        service.create_category(service.owner_token, payload, idempotency_key="cat-salary-main-again")


def test_record_expense_and_income_with_categories_and_summary(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY", "opening_balance": "1000"},
        idempotency_key="cat-cash",
    )
    expense_category, _ = service.create_category(
        service.owner_token,
        {"kind": "expense", "primary": "餐饮", "secondary": "午餐"},
        idempotency_key="cat-lunch",
    )
    income_category, _ = service.create_category(
        service.owner_token,
        {"kind": "income", "primary": "工资", "secondary": "主业"},
        idempotency_key="cat-job",
    )

    expense_tx, _ = service.record_expense(
        service.owner_token,
        {
            "amount": "38",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": expense_category.category_id,
            "purpose": "lunch",
            "occurred_at": "2026-05-16T12:30:00+08:00",
        },
        idempotency_key="expense-lunch",
    )
    income_tx, _ = service.record_income(
        service.owner_token,
        {
            "amount": "100",
            "currency": "CNY",
            "to_account_id": cash.account_id,
            "category_id": income_category.category_id,
            "purpose": "salary",
            "occurred_at": "2026-05-16T18:00:00+08:00",
        },
        idempotency_key="income-salary",
    )

    assert expense_tx.category_id == expense_category.category_id
    assert income_tx.category_id == income_category.category_id
    assert expense_tx.lines[0].category_id == expense_category.category_id
    assert expense_tx.lines[0].category_path_snapshot["path"] == "餐饮 / 午餐"
    assert service.account_balance(service.owner_token, cash.account_id)["official_balance"]["amount"] == "1062"

    expense_summary = service.category_summary(service.owner_token, kind="expense", currency="CNY")
    assert expense_summary["groups"] == [
        {
            "category_id": expense_category.category_id,
            "kind": "expense",
            "primary": "餐饮",
            "secondary": "午餐",
            "currency": "CNY",
            "amount": "38",
            "transaction_count": 1,
            "transaction_ids": [expense_tx.transaction_id],
        }
    ]

    income_summary = service.category_summary(service.owner_token, kind="income", currency="CNY")
    assert income_summary["groups"][0]["amount"] == "100"
    assert service.list_transactions(service.owner_token, category_id=expense_category.category_id) == [expense_tx]


def test_transaction_category_kind_must_match_flow_direction(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    cash, _ = service.create_account(
        service.owner_token,
        {"name": "Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="cat-flow-cash",
    )
    food, _ = service.create_account(
        service.owner_token,
        {"name": "Food", "type": "expense", "currency": "CNY"},
        idempotency_key="cat-flow-food",
    )
    income_category, _ = service.create_category(
        service.owner_token,
        {"kind": "income", "primary": "工资"},
        idempotency_key="cat-flow-income",
    )

    with pytest.raises(ValidationError):
        service.record_transaction(
            service.owner_token,
            {
                "amount": "10",
                "currency": "CNY",
                "from_account_id": cash.account_id,
                "to_account_id": food.account_id,
                "category_id": income_category.category_id,
                "purpose": "bad category",
            },
            idempotency_key="bad-category-flow",
        )


def test_categories_and_transaction_links_persist(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    cash, _ = first.create_account(
        token,
        {"name": "Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="persist-category-cash",
    )
    category, _ = first.create_category(
        token,
        {"kind": "expense", "primary": "交通", "secondary": "打车"},
        idempotency_key="persist-category",
    )
    transaction, _ = first.record_expense(
        token,
        {
            "amount": "21",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": category.category_id,
            "purpose": "taxi",
        },
        idempotency_key="persist-category-tx",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert second.get_category(token, category.category_id).secondary == "打车"
    assert second.ledger.transactions[transaction.transaction_id].category_id == category.category_id
    assert second.ledger.transactions[transaction.transaction_id].lines[0].category_id == category.category_id
