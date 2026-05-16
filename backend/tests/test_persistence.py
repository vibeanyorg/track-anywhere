from __future__ import annotations

import sqlite3

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_sqlite_persistence_survives_service_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token

    cash, _ = first.create_account(
        token,
        {"name": "Persisted Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="persist-cash",
    )
    food, _ = first.create_account(
        token,
        {"name": "Persisted Food", "type": "expense", "currency": "CNY"},
        idempotency_key="persist-food",
    )
    transaction, _ = first.record_transaction(
        token,
        {
            "occurred_at": "2026-05-16T12:30:00+08:00",
            "amount": "25",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": food.account_id,
            "purpose": "lunch",
        },
        idempotency_key="persist-lunch",
    )

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert second.owner_token == token
    assert second.actor_from_token(token, "account:read").actor_id == "owner"
    assert second.ledger.get_account(cash.account_id).name == "Persisted Cash"
    assert second.ledger.transactions[transaction.transaction_id].purpose == "lunch"
    assert second.account_balance(token, cash.account_id)["official_balance"]["amount"] == "75"


def test_idempotency_receipts_persist_across_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token

    account, replay = first.create_account(
        token,
        {"name": "Replay Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="persist-replay",
    )
    assert replay is False

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    replayed, replay = second.create_account(
        token,
        {"name": "Replay Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="persist-replay",
    )

    assert replay is True
    assert replayed["account_id"] == account.account_id


def test_users_persist_across_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = first.owner_token

    user, replay = first.create_user(
        token,
        {"username": "xyy", "display_name": "xyy"},
        idempotency_key="persist-user-xyy",
    )
    assert replay is False

    second = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert second.users.get(user.user_id).username == "xyy"
    assert [item.username for item in second.list_users(token)] == ["xyy"]


def test_sqlite_account_metadata_migration_adds_missing_columns(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            create table accounts (
                account_id varchar(80) primary key,
                name varchar(120) not null,
                type varchar(32) not null,
                currency varchar(3) not null,
                version integer not null
            )
            """
        )
        connection.execute(
            "insert into accounts (account_id, name, type, currency, version) values (?, ?, ?, ?, ?)",
            ("acc_legacy", "Legacy Cash", "asset", "CNY", 1),
        )

    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")

    account = service.ledger.get_account("acc_legacy")
    assert account.name == "Legacy Cash"
    assert account.institution_type is None
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("pragma table_info(accounts)").fetchall()}
    assert {"institution_type", "subtype", "institution"} <= columns
