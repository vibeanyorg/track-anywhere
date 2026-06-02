from __future__ import annotations

from rich.console import Console

from track_anywhere.balance_semantics import (
    ACCOUNT_TYPE_BALANCE_SEMANTICS,
    CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS,
    LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
    LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
)
from track_anywhere.posting_semantics import (
    DEBIT_CREDIT_AMOUNT_RULE,
    DEBIT_CREDIT_SIDE_RULE,
    LEGACY_SIGNED_SCOPE,
    POSTING_CANONICAL_MODEL,
)
from track_anywhere_cli.presenters import presenter_for


def test_public_presenters_render_real_payload_fields():
    cases = [
        (
            "summary.accounts",
            {
                "groups": [
                    {
                        "key": "ewallet",
                        "currency": "USD",
                        "asset_amount": "100",
                        "fund_amount": "25",
                        "liability_amount": "7",
                        "liability_outstanding_amount": "10",
                        "liability_overpayment_amount": "3",
                        "net_amount": "118",
                    }
                ]
            },
            ("Account summary", "ewallet", "Funds", "Liabilities owed", "Liability overpaid", "25", "10", "3", "118"),
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
                    "posting_semantics": {
                        "canonical_model": POSTING_CANONICAL_MODEL,
                        "row_model": "debit_credit",
                        "debit_credit_amount_rule": DEBIT_CREDIT_AMOUNT_RULE,
                        "debit_credit_side_rule": DEBIT_CREDIT_SIDE_RULE,
                    },
                    "postings": [
                        {"side": "credit", "amount": "38", "currency": "CNY", "amount_semantics": "debit_credit"},
                        {"side": "debit", "amount": "38", "currency": "CNY", "amount_semantics": "debit_credit"},
                    ],
                }
            },
                (
                    "Transaction recorded",
                    "txn_1",
                    "Lunch",
                    "Lunch expense",
                    POSTING_CANONICAL_MODEL,
                    "debit_credit",
                    DEBIT_CREDIT_AMOUNT_RULE,
                    "posting side is the only persisted debit/credit direction",
                    "do not infer direction from amount",
                    "credit 38 CNY (debit_credit, positive amount)",
                    "debit 38 CNY (debit_credit, positive amount)",
                ),
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
            {
                "transaction": {
                    "transaction_id": "txn_4",
                    "memo": "Reversal",
                    "postings": [{"side": "debit", "amount": "10", "currency": "USD", "amount_semantics": "debit_credit"}],
                }
            },
            ("Reversed transaction", "txn_4", "Reversal", "debit 10 USD (debit_credit, positive amount)"),
        ),
        (
            "balance.adjust",
            {"transaction": {"transaction_id": "txn_5", "purpose": "Cash correction", "lines": [{"amount": "10", "currency": "USD"}]}},
            ("Account adjustment", "txn_5", "Cash correction", "10 USD"),
        ),
        (
            "account.balance",
            {
                "account_id": "acc_card",
                "account_type": "liability",
                "currency": "USD",
                "balance_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                "official_balance": {
                    "amount": "9.36",
                    "amount_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                    "source": "confirmed_postings",
                    "as_of_ledger_version": 3,
                },
                    "liability_balance": {
                        "semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                        "outstanding_amount": "9.36",
                        "outstanding_amount_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
                        "overpayment_amount": "0",
                        "overpayment_amount_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
                    },
            },
            (
                "Account balance",
                "acc_card",
                ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                "Outstanding amount",
                "9.36",
                LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
                "Overpayment amount",
                "0",
                LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
            ),
        ),
        (
            "credit_card.show",
            {
                "credit_card": {
                    "account": {"account_id": "acc_card", "name": "Visa", "type": "liability", "currency": "USD"},
                    "natural_balance": "9.36",
                    "natural_balance_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                    "balance_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                    "outstanding_balance": "9.36",
                    "outstanding_balance_semantics": LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
                    "overpayment_balance": "0",
                    "overpayment_balance_semantics": LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
                    "derived_available_credit": "990.64",
                    "derived_available_credit_semantics": CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS,
                }
            },
            (
                "Credit card",
                "acc_card",
                "Natural liability balance",
                "Natural balance semantics",
                ACCOUNT_TYPE_BALANCE_SEMANTICS["liability"],
                "9.36",
                "Outstanding balance semantics",
                LIABILITY_OUTSTANDING_AMOUNT_SEMANTICS,
                "Overpayment balance semantics",
                LIABILITY_OVERPAYMENT_AMOUNT_SEMANTICS,
                "Derived available credit",
                "990.64",
                CREDIT_CARD_DERIVED_AVAILABLE_CREDIT_SEMANTICS,
            ),
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
            "system.posting_semantics.rewrite",
            {
                "status": "rewritten",
                "book_id": "book_default",
                "confirmed_postings_rewritten": 2,
                "posting_semantics": {
                    "canonical_model": POSTING_CANONICAL_MODEL,
                    "debit_credit_amount_rule": DEBIT_CREDIT_AMOUNT_RULE,
                    "debit_credit_side_rule": DEBIT_CREDIT_SIDE_RULE,
                    "legacy_signed_scope": LEGACY_SIGNED_SCOPE,
                },
            },
            (
                "Posting semantics write",
                "Rewritten",
                "Canonical model",
                POSTING_CANONICAL_MODEL,
                "Side rule",
                "posting side is the only persisted debit/credit direction",
                "do not infer direction",
                "Legacy signed scope",
                LEGACY_SIGNED_SCOPE,
            ),
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
