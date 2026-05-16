from __future__ import annotations

from decimal import Decimal

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_investment_events_drive_holding_period_and_annualized_return():
    local = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = local.owner_token
    account, _ = local.create_account(
        token,
        {
            "name": "工银理财25G2501A",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "35051.93",
            "institution_type": "bank",
            "subtype": "wealth_management",
            "institution": "工商银行",
        },
        idempotency_key="investment-account",
    )

    event, replay = local.record_investment_event(
        token,
        {
            "account_id": account.account_id,
            "event_type": "buy",
            "amount": "35000",
            "currency": "CNY",
            "occurred_at": "2026-04-24T00:00:00+08:00",
            "memo": "initial purchase",
        },
        idempotency_key="investment-buy",
    )
    performance = local.investment_performance(
        token,
        account.account_id,
        as_of="2026-05-15T00:00:00+08:00",
    )

    assert replay is False
    assert event.account_id == account.account_id
    assert performance["current_value"] == "35051.93"
    assert performance["contributions"] == "35000"
    assert performance["net_contributed"] == "35000"
    assert performance["total_return"] == "51.93"
    assert performance["holding_days"] == 21
    assert Decimal(performance["money_weighted_annualized_return"]) > Decimal("0")


def test_investment_event_additions_are_included_in_cash_flows():
    local = FinanceService(DeploymentSecurityConfig(), database_url="sqlite:///:memory:")
    token = local.owner_token
    account, _ = local.create_account(
        token,
        {
            "name": "加仓测试理财",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "15200",
            "institution_type": "bank",
            "subtype": "wealth_management",
            "institution": "测试银行",
        },
        idempotency_key="investment-add-account",
    )

    local.record_investment_event(
        token,
        {
            "account_id": account.account_id,
            "event_type": "buy",
            "amount": "10000",
            "currency": "CNY",
            "occurred_at": "2026-01-01T00:00:00+08:00",
        },
        idempotency_key="investment-add-buy",
    )
    local.record_investment_event(
        token,
        {
            "account_id": account.account_id,
            "event_type": "add",
            "amount": "5000",
            "currency": "CNY",
            "occurred_at": "2026-03-01T00:00:00+08:00",
        },
        idempotency_key="investment-add-add",
    )

    performance = local.investment_performance(
        token,
        account.account_id,
        as_of="2026-04-01T00:00:00+08:00",
    )

    assert performance["event_count"] == 2
    assert performance["contributions"] == "15000"
    assert performance["net_contributed"] == "15000"
    assert performance["total_return"] == "200"
    assert performance["cash_flows"] == [
        {"amount": "-10000", "date": "2026-01-01T00:00:00+08:00", "event_type": "buy"},
        {"amount": "-5000", "date": "2026-03-01T00:00:00+08:00", "event_type": "add"},
        {"amount": "15200", "date": "2026-04-01T00:00:00+08:00", "event_type": "current_value"},
    ]


def test_investment_events_persist_across_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token
    account, _ = first.create_account(
        token,
        {
            "name": "Persisted Wealth",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "100.50",
            "institution_type": "bank",
            "subtype": "wealth_management",
            "institution": "测试银行",
        },
        idempotency_key="persist-investment-account",
    )
    event, _ = first.record_investment_event(
        token,
        {
            "account_id": account.account_id,
            "event_type": "buy",
            "amount": "100",
            "currency": "CNY",
            "occurred_at": "2026-05-01T00:00:00+08:00",
            "memo": "persist me",
        },
        idempotency_key="persist-investment-event",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    events = second.list_investment_events(token, account.account_id)

    assert [item.event_id for item in events] == [event.event_id]
    assert second.investment_performance(
        token,
        account.account_id,
        as_of="2026-05-16T00:00:00+08:00",
    )["total_return"] == "0.50"


def test_api_investment_event_and_performance_routes():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}

    account_resp = client.post(
        "/api/v1/accounts",
        json={
            "name": "API Wealth",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "1000.20",
            "institution_type": "bank",
            "subtype": "wealth_management",
            "institution": "API Bank",
        },
        headers={**headers, "X-Idempotency-Key": "api-investment-account"},
    )
    account_id = account_resp.json()["account"]["account_id"]

    event_resp = client.post(
        "/api/v1/investments/events",
        json={
            "account_id": account_id,
            "event_type": "buy",
            "amount": "1000",
            "currency": "CNY",
            "occurred_at": "2026-05-01T00:00:00+08:00",
        },
        headers={**headers, "X-Idempotency-Key": "api-investment-buy"},
    )
    performance_resp = client.get(
        f"/api/v1/investments/accounts/{account_id}/performance?as_of=2026-05-16T00%3A00%3A00%2B08%3A00",
        headers=headers,
    )

    assert event_resp.status_code == 200
    assert event_resp.json()["event"]["event_type"] == "buy"
    assert performance_resp.status_code == 200
    assert performance_resp.json()["holding_days"] == 15
    assert performance_resp.json()["total_return"] == "0.20"
