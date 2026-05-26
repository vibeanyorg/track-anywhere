from __future__ import annotations

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_payment_instrument_commands_use_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        if method == "POST":
            return 200, {"payment_instrument": {"instrument_id": "pi_1", **payload}}
        if path.endswith("/bocom-2862"):
            return 200, {"payment_instrument": {"slug": "bocom-2862", "account_id": "acc_card"}}
        return 200, {"payment_instruments": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "payment",
                "instrument",
                "create",
                "bocom-2862",
                "--display-name",
                "交通银行实体卡(2862)",
                "--kind",
                "credit-card",
                "--account-id",
                "acc_card",
                "--last4",
                "2862",
                "--idempotency-key",
                "instrument-create",
                "--json",
            ]
        )
        == 0
    )
    assert main(["--token", "token-1", "payment", "instrument", "list", "--account-id", "acc_card", "--json"]) == 0
    assert main(["--token", "token-1", "payment", "instrument", "show", "bocom-2862", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/payment-instruments",
            "payload": {
                "slug": "bocom-2862",
                "display_name": "交通银行实体卡(2862)",
                "kind": "credit_card",
                "account_id": "acc_card",
                "last4": "2862",
            },
            "key": "instrument-create",
        },
        {
            "method": "GET",
            "path": "/api/v1/payment-instruments?account_id=acc_card&status=active",
            "payload": None,
            "key": None,
        },
        {"method": "GET", "path": "/api/v1/payment-instruments/bocom-2862", "payload": None, "key": None},
    ]
