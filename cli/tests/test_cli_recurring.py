from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_recurring_create_posts_paid_monthly_item(monkeypatch, capsys):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"recurring_item": {"recurring_id": "rec_1", "name": payload["name"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "recurring",
                "create",
                "--name",
                "ChatGPT",
                "--kind",
                "paid",
                "--amount",
                "20",
                "--currency",
                "USD",
                "--monthly-day",
                "15",
                "--anchor-date",
                "2026-06-15",
                "--remind",
                "3",
                "--remind",
                "2",
                "--remind",
                "1",
                "--source-account-id",
                "acc_usd",
                "--category-id",
                "cat_ai",
                "--provider",
                "OpenAI",
                "--idempotency-key",
                "rec-chatgpt",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "POST",
            "path": "/api/v1/recurring/items",
            "payload": {
                "name": "ChatGPT",
                "kind": "paid",
                "amount": "20",
                "currency": "USD",
                "provider": "OpenAI",
                "recurrence": {"type": "monthly_day", "day": 15},
                "anchor_date": "2026-06-15",
                "reminder_days": [3, 2, 1],
                "source_account_id": "acc_usd",
                "category_id": "cat_ai",
            },
            "key": "rec-chatgpt",
            "token": "token-1",
        }
    ]
    assert json.loads(capsys.readouterr().out)["recurring_item"]["recurring_id"] == "rec_1"


def test_recurring_reminders_and_draft_generation_routes(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"ok": True}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "recurring", "reminders", "--as-of", "2026-06-12", "--json"]) == 0
    assert (
        main(
            [
                "--token",
                "token-1",
                "recurring",
                "draft-due",
                "--as-of",
                "2026-06-16",
                "--idempotency-key",
                "recurring-run",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "GET",
            "path": "/api/v1/recurring/reminders?as_of=2026-06-12&window_days=0",
            "payload": None,
            "key": None,
        },
        {
            "method": "POST",
            "path": "/api/v1/recurring/drafts",
            "payload": {"as_of": "2026-06-16"},
            "key": "recurring-run",
        },
    ]


def test_recurring_update_posts_status_patch(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key})
        return 200, {"recurring_item": {"recurring_id": "rec_1", "status": payload["status"]}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--token",
                "token-1",
                "recurring",
                "update",
                "rec_1",
                "--status",
                "cancelled",
                "--idempotency-key",
                "rec-cancel",
                "--json",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "method": "PATCH",
            "path": "/api/v1/recurring/items/rec_1",
            "payload": {"status": "cancelled"},
            "key": "rec-cancel",
        }
    ]
