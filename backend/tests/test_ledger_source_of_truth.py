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
