from __future__ import annotations

from rich.console import Console

from track_anywhere_cli.presenters import presenter_for


def test_public_presenters_render_real_payload_fields():
    cases = [
        (
            "summary.accounts",
            {"groups": [{"key": "ewallet", "currency": "USD", "asset_amount": "100", "liability_amount": "10", "net_amount": "90"}]},
            ("Account summary", "ewallet", "90"),
        ),
        (
            "category.create",
            {"category": {"category_id": "cat_1", "kind": "expense", "primary": "Food", "secondary": "Delivery"}},
            ("Category", "cat_1", "Delivery"),
        ),
        (
            "user.create",
            {"user": {"user_id": "user_1", "username": "xyy", "display_name": "XYY"}},
            ("User", "user_1", "xyy"),
        ),
        (
            "tx.record",
            {
                "transaction": {
                    "transaction_id": "txn_1",
                    "memo": "Lunch",
                    "purpose": "Lunch expense",
                    "occurred_at": "2026-05-16T12:30:00+08:00",
                    "postings": [
                        {"amount": "-38", "currency": "CNY"},
                        {"amount": "38", "currency": "CNY"},
                    ],
                }
            },
            ("Transaction recorded", "txn_1", "Lunch", "Lunch expense", "38 CNY"),
        ),
        (
            "tx.list",
            {
                "transactions": [
                    {
                        "transaction_id": "txn_2",
                        "memo": "Coffee",
                        "purpose": "Morning coffee",
                        "lines": [{"amount": "6", "currency": "USD"}],
                    }
                ]
            },
            ("Transactions", "txn_2", "Coffee", "6 USD"),
        ),
        (
            "draft.confirm",
            {"transaction": {"transaction_id": "txn_3", "memo": "Confirmed draft"}, "idempotent_replay": False},
            ("Draft confirm", "txn_3", "Confirmed draft", "False"),
        ),
        (
            "tx.reverse",
            {"transaction": {"transaction_id": "txn_4", "memo": "Reversal", "postings": [{"amount": "-10", "currency": "USD"}]}},
            ("Reversed transaction", "txn_4", "Reversal", "-10 USD"),
        ),
        (
            "balance.adjust",
            {"transaction": {"transaction_id": "txn_5", "purpose": "Cash correction", "lines": [{"amount": "10", "currency": "USD"}]}},
            ("Account adjustment", "txn_5", "Cash correction", "10 USD"),
        ),
        (
            "investment.performance",
            {
                "account_id": "acc_wealth",
                "currency": "CNY",
                "holding_days": 21,
                "total_return": "120",
            },
            ("Investment performance", "acc_wealth", "21", "120"),
        ),
        (
            "recurring.create",
            {
                "recurring_item": {
                    "recurring_id": "rec_1",
                    "name": "ChatGPT",
                    "kind": "paid",
                    "recurrence": {"type": "monthly_day", "day": 15},
                }
            },
            ("Recurring item created", "rec_1", "ChatGPT", "monthly day 15"),
        ),
        (
            "recurring.update",
            {"recurring_item": {"recurring_id": "rec_2", "name": "Netflix", "status": "paused"}},
            ("Recurring item updated", "rec_2", "Netflix", "paused"),
        ),
    ]

    for command_path, payload, expected_fragments in cases:
        console = Console(record=True, width=120)
        console.print(presenter_for(command_path)(payload))
        rendered = console.export_text()

        for fragment in expected_fragments:
            assert fragment in rendered


def test_unknown_presenter_fails():
    import pytest

    with pytest.raises(KeyError):
        presenter_for("unknown.command")


def test_known_command_paths_use_registered_presenters():
    for command_path in (
        "summary.accounts",
        "category.create",
        "tx.show",
        "auth.status",
        "data.backup",
        "investment.performance",
        "recurring.show",
    ):
        renderable = presenter_for(command_path)({"status": "ok"})

        assert not isinstance(renderable, dict)
        assert hasattr(renderable, "__rich_console__")
