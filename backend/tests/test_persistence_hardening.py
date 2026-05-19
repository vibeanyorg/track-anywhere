from __future__ import annotations

import sqlite3

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
    assert replayed["memo"] == "secret lunch"
    assert second.account_balance(token, cash.account_id)["official_balance"]["amount"] == "90"


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
