from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_category_commands_and_category_summary_use_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        if method == "POST":
            return 200, {"category": {"category_id": "cat_1", **payload}}
        return 200, {"categories": [], "groups": []}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "category",
                "create",
                "--kind",
                "expense",
                "--primary",
                "餐饮",
                "--secondary",
                "外卖",
                "--idempotency-key",
                "cat-food-delivery",
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
                "category",
                "find",
                "--kind",
                "expense",
                "--primary",
                "餐饮",
                "--secondary",
                "外卖",
                "--json",
            ]
        )
        == 0
    )
    assert main(["--token", "token-1", "summary", "categories", "--kind", "expense", "--currency", "CNY", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/categories",
            "payload": {"kind": "expense", "primary": "餐饮", "secondary": "外卖"},
            "key": "cat-food-delivery",
        },
        {
            "method": "GET",
            "path": "/api/v1/categories?kind=expense&primary=%E9%A4%90%E9%A5%AE&secondary=%E5%A4%96%E5%8D%96",
            "payload": None,
            "key": None,
        },
        {
            "method": "GET",
            "path": "/api/v1/summary/categories?kind=expense&currency=CNY",
            "payload": None,
            "key": None,
        },
    ]


def test_credit_card_commands_use_profile_api(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        if method == "PATCH":
            return 200, {"credit_card": {"account": {"account_id": "acc_card"}, "profile": payload}}
        return 200, {"credit_cards": [], "credit_card": {"account": {"account_id": "acc_card"}}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "credit-card", "list", "--json"]) == 0
    assert main(["--token", "token-1", "credit-card", "show", "acc_card", "--json"]) == 0
    assert (
        main(
            [
                "--token",
                "token-1",
                "credit-card",
                "update",
                "acc_card",
                "--credit-limit",
                "10000",
                "--available-credit",
                "9700",
                "--statement-day",
                "8",
                "--due-day",
                "26",
                "--annual-fee",
                "0",
                "--idempotency-key",
                "card-profile",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {"method": "GET", "path": "/api/v1/credit-cards", "payload": None, "key": None},
        {"method": "GET", "path": "/api/v1/credit-cards/acc_card", "payload": None, "key": None},
        {
            "method": "PATCH",
            "path": "/api/v1/credit-cards/acc_card",
            "payload": {
                "credit_limit": "10000",
                "available_credit": "9700",
                "statement_day": 8,
                "due_day": 26,
                "annual_fee": "0",
            },
            "key": "card-profile",
        },
    ]


def test_user_create_posts_payload(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"user": {"user_id": "user_1", "username": payload["username"], "display_name": payload["display_name"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "user", "create", "xyy", "--display-name", "XYY", "--json"]) == 0

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/users",
            "payload": {"username": "xyy", "display_name": "XYY"},
            "key": calls[0]["key"],
            "token": "token-1",
        }
    ]
    assert calls[0]["key"].startswith("user-create-")
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "user.create"
    assert payload["data"]["user"]["username"] == "xyy"


def test_category_command_human_output_is_rich_table(monkeypatch, capsys):
    def fake_request(config, method, path, payload=None, key=None):
        return 200, {
            "categories": [
                {
                    "category_id": "cat_1",
                    "kind": "expense",
                    "primary": "餐饮",
                    "secondary": "外卖",
                }
            ]
        }

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "category",
                "list",
                "--kind",
                "expense",
                "--primary",
                "餐饮",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert output.lstrip() and not output.lstrip().startswith("{")
    assert "Categories" in output
    assert "cat_1" in output
    assert "expense" in output
    assert "餐饮" in output
