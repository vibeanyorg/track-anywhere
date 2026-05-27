from __future__ import annotations

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_investment_reads_use_storage_truth_when_memory_maps_are_stale(tmp_path):
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}")
    token = service.owner_token
    wealth, _ = service.create_account(
        token,
        {"name": "Investment Read Truth Wealth", "type": "asset", "currency": "CNY"},
        idempotency_key="investment-read-truth-wealth",
    )
    service.record_investment_valuation(
        token,
        {
            "account_id": wealth.account_id,
            "value": "300",
            "currency": "CNY",
            "observed_at": "2026-05-20T00:00:00+08:00",
            "source": "statement",
        },
        idempotency_key="investment-read-truth-valuation",
    )
    service.ledger.accounts[wealth.account_id].currency = "USD"
    service.ledger.accounts[wealth.account_id].book_id = "stale_book"

    events = service.list_investment_events(token, wealth.account_id)
    valuations = service.list_investment_valuations(token, wealth.account_id)
    performance = service.investment_performance(token, wealth.account_id)

    assert events == []
    assert valuations[0].currency == "CNY"
    assert performance["currency"] == "CNY"
    assert performance["current_value"] == "300"
