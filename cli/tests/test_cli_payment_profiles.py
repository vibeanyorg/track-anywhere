from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import EXIT_VALIDATION, main


def _json_from_output(captured):
    return json.loads(captured.out or captured.err)


def test_payment_profile_commands_use_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        if method == "POST":
            return 200, {"payment_profile": {"profile_id": "pp_1", **payload}}
        if path.endswith("/status"):
            return 200, {"payment": "safepal", "effective_instrument_balance": {"amount": "10.00", "currency": "USD"}}
        return 200, {"payment_profiles": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "payment",
                "profile",
                "create",
                "safepal",
                "--display-name",
                "SafePal",
                "--kind",
                "token-backed-card",
                "--instrument-account-id",
                "acc_card",
                "--backing-account-id",
                "acc_usd24",
                "--idempotency-key",
                "profile-create",
                "--json",
            ]
        )
        == 0
    )
    assert main(["--token", "token-1", "payment", "profile", "list", "--json"]) == 0
    assert main(["--token", "token-1", "payment", "profile", "status", "safepal", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/payment-profiles",
            "payload": {
                "slug": "safepal",
                "display_name": "SafePal",
                "kind": "token_backed_card",
                "instrument_account_id": "acc_card",
                "backing_account_id": "acc_usd24",
                "settlement_mode": "immediate",
                "settlement_rate": "1",
            },
            "key": "profile-create",
        },
        {"method": "GET", "path": "/api/v1/payment-profiles?status=active", "payload": None, "key": None},
        {"method": "GET", "path": "/api/v1/payment-profiles/safepal/status", "payload": None, "key": None},
    ]


def test_expense_record_with_payment_uses_payment_profile_endpoint(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"transaction": {"transaction_id": "txn_1", "postings": []}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "expense",
                "record",
                "--payment",
                "safepal",
                "--amount",
                "3.40",
                "--currency",
                "USD",
                "--category-id",
                "cat_food",
                "--purpose",
                "Meituan",
                "--memo",
                "SafePal card",
                "--idempotency-key",
                "safepal-expense",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/payment-profiles/safepal/expenses",
            "payload": {
                "amount": "3.40",
                "currency": "USD",
                "category_id": "cat_food",
                "purpose": "Meituan",
                "memo": "SafePal card",
            },
            "key": "safepal-expense",
        }
    ]


def test_expense_record_rejects_payment_and_from_account(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"transaction": {"transaction_id": "txn_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(
        [
            "--token",
            "token-1",
            "expense",
            "record",
            "--payment",
            "safepal",
            "--from",
            "acc_cash",
            "--amount",
            "3.40",
            "--currency",
            "USD",
            "--category-id",
            "cat_food",
            "--purpose",
            "Meituan",
            "--json",
        ]
    )

    payload = _json_from_output(capsys.readouterr())
    assert exit_code == EXIT_VALIDATION
    assert payload["ok"] is False
    assert payload["command"] == "cli.parse"
    assert "exactly one of --payment or --from-account-id" in payload["data"]["detail"]
    assert calls == []
