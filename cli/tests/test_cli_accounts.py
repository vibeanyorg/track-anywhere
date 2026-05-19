from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_account_read_commands_use_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"accounts": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "list",
                "--name",
                "Visa",
                "--currency",
                "USD",
                "--institution-type",
                "bank",
                "--subtype",
                "credit_card",
                "--json",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "find",
                "--name",
                "Visa",
                "--type",
                "liability",
                "--institution",
                "广发",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/accounts?name=Visa&currency=USD&institution_type=bank&subtype=credit_card",
            "payload": None,
            "key": None,
        },
        {
            "method": "GET",
            "path": "/api/v1/accounts?name=Visa&type=liability&institution=%E5%B9%BF%E5%8F%91",
            "payload": None,
            "key": None,
        },
    ]


def test_account_create_and_update_metadata_payloads(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"account": {"account_id": "acc_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "create",
                "Wise USD",
                "--currency",
                "USD",
                "--institution-type",
                "fintech",
                "--subtype",
                "multicurrency_wallet",
                "--institution",
                "Wise",
                "--idempotency-key",
                "acct-wise",
                "--json",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "update",
                "acc_1",
                "--institution-type",
                "bank",
                "--subtype",
                "savings",
                "--institution",
                "Example Bank",
                "--idempotency-key",
                "acct-update",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/accounts",
            "payload": {
                "name": "Wise USD",
                "type": "asset",
                "currency": "USD",
                "opening_balance": "0",
                "institution_type": "fintech",
                "subtype": "multicurrency_wallet",
                "institution": "Wise",
            },
            "key": "acct-wise",
        },
        {
            "method": "PATCH",
            "path": "/api/v1/accounts/acc_1",
            "payload": {"institution_type": "bank", "subtype": "savings", "institution": "Example Bank"},
            "key": "acct-update",
        },
    ]


def test_summary_accounts_uses_query_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"groups": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "summary",
                "accounts",
                "--group-by",
                "institution_type",
                "--currency",
                "CNY",
                "--institution-type",
                "e_wallet",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/summary/accounts?group_by=institution_type&currency=CNY&institution_type=e_wallet",
            "payload": None,
            "key": None,
        }
    ]


def test_account_list_command_returns_enveloped_json_payload(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"accounts": [{"account_id": "acc_1"}]}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "account",
                "list",
                "--name",
                "Visa",
                "--currency",
                "USD",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "account.list"
    assert payload["data"]["accounts"] == [{"account_id": "acc_1"}]
    assert calls == [
        {"method": "GET", "path": "/api/v1/accounts?name=Visa&currency=USD", "payload": None, "key": None},
    ]
