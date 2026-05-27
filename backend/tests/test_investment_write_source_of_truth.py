from __future__ import annotations

from decimal import Decimal

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_cash_backed_investment_event_uses_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Investment Truth Cash", "type": "asset", "currency": "CNY", "opening_balance": "1000"},
        idempotency_key="investment-truth-cash",
    )
    wealth, _ = service.create_account(
        token,
        {"name": "Investment Truth Wealth", "type": "asset", "currency": "CNY"},
        idempotency_key="investment-truth-wealth",
    )

    def fail_create_transaction(*_args, **_kwargs):
        raise AssertionError("investment event called legacy in-memory transaction factory")

    service.ledger.create_transaction = fail_create_transaction
    service.ledger.accounts[cash.account_id].currency = "USD"
    service.ledger.accounts[wealth.account_id].book_id = "stale_book"

    event, replay = service.record_investment_event(
        token,
        {
            "account_id": wealth.account_id,
            "event_type": "buy",
            "amount": "200",
            "currency": "CNY",
            "cash_account_id": cash.account_id,
            "occurred_at": "2026-05-20T00:00:00+08:00",
            "memo": "storage truth investment buy",
        },
        idempotency_key="investment-truth-buy",
    )

    assert replay is False
    assert event.transaction_id is not None
    linked = service.get_transaction(token, event.transaction_id)
    assert linked.purpose == "investment_buy"
    assert [line.line_type for line in linked.lines] == ["investment_buy"]
    assert Decimal(service.account_balance(token, cash.account_id)["official_balance"]["amount"]) == Decimal("800")
    assert Decimal(service.account_balance(token, wealth.account_id)["official_balance"]["amount"]) == Decimal("200")


def test_investment_valuation_uses_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    wealth, _ = service.create_account(
        token,
        {"name": "Valuation Truth Wealth", "type": "asset", "currency": "CNY"},
        idempotency_key="valuation-truth-wealth",
    )
    service.ledger.accounts[wealth.account_id].currency = "USD"
    service.ledger.accounts[wealth.account_id].book_id = "stale_book"

    valuation, replay = service.record_investment_valuation(
        token,
        {
            "account_id": wealth.account_id,
            "value": "300",
            "currency": "CNY",
            "observed_at": "2026-05-20T00:00:00+08:00",
            "source": "statement",
            "memo": "storage truth valuation",
        },
        idempotency_key="valuation-truth-record",
    )

    assert replay is False
    assert valuation.account_id == wealth.account_id
    assert valuation.currency == "CNY"
