from __future__ import annotations

import sqlite3
from decimal import Decimal

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_confirmed_ledger_reads_use_database_source_of_truth(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    account, _ = service.create_account(
        token,
        {"name": "Truth Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="truth-cash",
    )
    transaction, _ = service.adjust_balance(
        token,
        {
            "account_id": account.account_id,
            "amount": "100",
            "currency": "CNY",
            "purpose": "stored truth",
        },
        idempotency_key="truth-balance",
    )
    transaction.purpose = "dirty in-memory purpose"
    transaction.postings[0].amount = Decimal("999")

    balance = service.account_balance(token, account.account_id)
    shown = service.get_transaction(token, transaction.transaction_id)
    listed = service.list_transactions(token, account_id=account.account_id, limit=1)
    summary = service.account_summary(token, group_by="currency", currency="CNY")

    assert balance["official_balance"]["amount"] == "100"
    assert shown.purpose == "stored truth"
    assert shown.postings[0].amount == Decimal("100")
    assert listed[0].purpose == "stored truth"
    assert summary["groups"][0]["amount"] == "100"


def test_startup_preserves_legacy_confirmed_amount_precision(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    database_url = f"sqlite:///{database_path}"
    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = service.owner_token
    wallet, _ = service.create_account(
        token,
        {"name": "Legacy USDT", "type": "asset", "currency": "USDT"},
        idempotency_key="legacy-usdt-wallet",
    )
    equity, _ = service.create_account(
        token,
        {"name": "Legacy USDT Equity", "type": "equity", "currency": "USDT"},
        idempotency_key="legacy-usdt-equity",
    )
    transaction_id = "txn_legacy_usdt_precision"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            insert into transactions (
                transaction_id, book_id, memo, occurred_at, purpose,
                reversed_by, reverses_transaction_id, version
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (transaction_id, "book_default", "", "2026-05-25T00:00:00+00:00", "legacy precision", None, None, 1),
        )
        connection.execute(
            """
            insert into postings (transaction_id, book_id, position, account_id, amount, currency)
            values (?, ?, ?, ?, ?, ?)
            """,
            (transaction_id, "book_default", 0, wallet.account_id, "0.12345678", "USDT"),
        )
        connection.execute(
            """
            insert into postings (transaction_id, book_id, position, account_id, amount, currency)
            values (?, ?, ?, ?, ?, ?)
            """,
            (transaction_id, "book_default", 1, equity.account_id, "-0.12345678", "USDT"),
        )

    restarted = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert restarted.account_balance(token, wallet.account_id)["official_balance"]["amount"] == "0.12345678"


def test_catalog_reads_use_database_source_of_truth(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token

    account, _ = service.create_account(
        token,
        {
            "name": "DB Truth Cash",
            "type": "asset",
            "currency": "CNY",
            "institution_type": "cash",
            "subtype": "cash",
            "institution": "DB Truth",
        },
        idempotency_key="catalog-truth-account",
    )
    category, _ = service.create_category(
        token,
        {"kind": "expense", "name": "DB Truth Food"},
        idempotency_key="catalog-truth-category",
    )
    recurring, _ = service.create_recurring_item(
        token,
        {
            "name": "DB Truth Reminder",
            "kind": "reminder_only",
            "recurrence": {"type": "monthly_day", "day": 5},
            "anchor_date": "2026-06-05",
            "reminder_days": [1],
        },
        idempotency_key="catalog-truth-recurring",
    )

    account.name = "stale memory account"
    account.institution = "stale memory institution"
    category.name = "stale memory category"
    category.primary = "stale memory category"
    category.path_cache = "stale memory category"
    recurring.name = "stale memory recurring"

    assert service.get_account(token, account.account_id).name == "DB Truth Cash"
    assert service.list_accounts(token, institution="DB Truth")[0].account_id == account.account_id
    assert service.get_category(token, category.category_id).name == "DB Truth Food"
    assert service.list_categories(token, kind="expense", name="DB Truth Food")[0].category_id == category.category_id
    assert service.find_category_by_path(token, kind="expense", path="DB Truth Food").category_id == category.category_id
    assert service.get_recurring_item(token, recurring.recurring_id).name == "DB Truth Reminder"
    assert service.list_recurring_items(token)[0].name == "DB Truth Reminder"


def test_payment_catalog_reads_use_database_source_of_truth(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    card, _ = service.create_account(
        token,
        {
            "name": "DB Truth Card",
            "type": "liability",
            "currency": "USD",
            "institution_type": "bank",
            "subtype": "credit_card",
        },
        idempotency_key="payment-truth-card",
    )
    backing, _ = service.create_account(
        token,
        {"name": "DB Truth USD24", "type": "asset", "currency": "USD24", "opening_balance": "20"},
        idempotency_key="payment-truth-usd24",
    )
    profile, _ = service.create_payment_profile(
        token,
        {
            "slug": "db-truth-safepal",
            "display_name": "DB Truth SafePal",
            "kind": "token_backed_card",
            "instrument_account_id": card.account_id,
            "backing_account_id": backing.account_id,
            "settlement_mode": "immediate",
            "settlement_rate": "1",
        },
        idempotency_key="payment-truth-profile",
    )
    instrument, _ = service.create_payment_instrument(
        token,
        {
            "slug": "db-truth-card",
            "display_name": "DB Truth Card Instrument",
            "kind": "credit_card",
            "account_id": card.account_id,
            "last4": "5964",
        },
        idempotency_key="payment-truth-instrument",
    )

    service.payment_profiles.profiles[profile.profile_id].display_name = "stale memory profile"
    service.payment_instruments.instruments[instrument.instrument_id].display_name = "stale memory instrument"
    card.name = "stale memory card"

    assert service.get_payment_profile(token, profile.profile_id).display_name == "DB Truth SafePal"
    assert service.resolve_payment_profile(token, "db-truth-safepal").profile_id == profile.profile_id
    assert service.list_payment_profiles(token)[0].display_name == "DB Truth SafePal"
    assert service.get_payment_instrument(token, instrument.instrument_id).display_name == "DB Truth Card Instrument"
    assert service.resolve_payment_instrument(token, "db-truth-card").instrument_id == instrument.instrument_id
    assert service.list_payment_instruments(token, account_id=card.account_id)[0].display_name == "DB Truth Card Instrument"
    overview = service.get_credit_card(token, card.account_id)
    assert overview["account"].name == "DB Truth Card"
    assert overview["instruments"][0].display_name == "DB Truth Card Instrument"
