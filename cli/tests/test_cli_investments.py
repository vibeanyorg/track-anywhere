from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_investment_event_cli_posts_dated_cash_flow(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"event": {"event_id": "inv_1", "event_type": payload["event_type"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "investment",
                "event",
                "acc_wealth",
                "--type",
                "buy",
                "--amount",
                "35000",
                "--currency",
                "CNY",
                "--occurred-at",
                "2026-04-24T00:00:00+08:00",
                "--memo",
                "initial purchase",
                "--idempotency-key",
                "inv-buy",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/investments/events",
            "payload": {
                "account_id": "acc_wealth",
                "event_type": "buy",
                "amount": "35000",
                "currency": "CNY",
                "occurred_at": "2026-04-24T00:00:00+08:00",
                "memo": "initial purchase",
            },
            "key": "inv-buy",
        }
    ]
    assert json.loads(capsys.readouterr().out)["event"]["event_type"] == "buy"


def test_investment_performance_cli_uses_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"account_id": "acc_wealth", "holding_days": 21}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "investment",
                "performance",
                "acc_wealth",
                "--as-of",
                "2026-05-15T00:00:00+08:00",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/investments/accounts/acc_wealth/performance?as_of=2026-05-15T00%3A00%3A00%2B08%3A00",
            "payload": None,
            "key": None,
        }
    ]
