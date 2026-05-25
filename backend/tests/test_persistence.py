from __future__ import annotations

import sqlite3

from sqlalchemy import event

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

    assert second.actor_from_token(token, "account:read").actor_id == "owner"
    assert second.ledger.get_account(cash.account_id).name == "Persisted Cash"
    assert second.ledger.transactions[transaction.transaction_id].purpose == "lunch"
    assert second.account_balance(token, cash.account_id)["official_balance"]["amount"] == "75"


def test_confirmed_transactions_keep_memo_separate_from_purpose(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = service.owner_token

    cash, _ = service.create_account(
        token,
        {"name": "Memo Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="memo-cash",
    )
    food, _ = service.create_account(
        token,
        {"name": "Memo Food", "type": "expense", "currency": "CNY"},
        idempotency_key="memo-food",
    )

    transaction, _ = service.record_transaction(
        token,
        {
            "amount": "25",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": food.account_id,
            "purpose": "meal",
            "memo": "Lunch with Alice, card ending 1234",
        },
        idempotency_key="memo-lunch",
    )
    no_memo_transaction, _ = service.record_transaction(
        token,
        {
            "amount": "5",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "to_account_id": food.account_id,
            "purpose": "snack",
        },
        idempotency_key="memo-snack",
    )

    restarted = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    assert transaction.purpose == "meal"
    assert transaction.memo == "Lunch with Alice, card ending 1234"
    assert no_memo_transaction.purpose == "snack"
    assert no_memo_transaction.memo == ""
    assert restarted.ledger.transactions[transaction.transaction_id].memo == "Lunch with Alice, card ending 1234"
    assert restarted.ledger.transactions[no_memo_transaction.transaction_id].memo == ""


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


def test_persistence_save_does_not_issue_table_wide_deletes(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    token = service.owner_token
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.lower().split()))

    event.listen(service.storage.engine, "before_cursor_execute", capture_statement)
    try:
        service.create_account(
            token,
            {"name": "Incremental Cash", "type": "asset", "currency": "CNY"},
            idempotency_key="incremental-cash",
        )
    finally:
        event.remove(service.storage.engine, "before_cursor_execute", capture_statement)

    table_wide_deletes = [
        statement
        for statement in statements
        if statement.startswith("delete from ") and " where " not in statement
    ]

    assert table_wide_deletes == []


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


def test_sqlite_schema_is_created_by_alembic_migrations(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"

    FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")

    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'").fetchall()}
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        account_columns = {row[1]: row[2] for row in connection.execute("pragma table_info(accounts)").fetchall()}
        draft_columns = {row[1] for row in connection.execute("pragma table_info(drafts)").fetchall()}
        investment_valuation_indexes = {row[1] for row in connection.execute("pragma index_list(investment_valuations)").fetchall()}
        posting_indexes = connection.execute("pragma index_list(postings)").fetchall()

    assert "alembic_version" in tables
    assert "accounts" in tables
    assert "transactions" in tables
    assert "postings" in tables
    assert "recurring_items" in tables
    assert version == "0011_posting_position_invariants"
    assert account_columns["currency"].upper() == "VARCHAR(16)"
    assert account_columns["book_id"].upper() == "VARCHAR(80)"
    assert {"category_id", "metadata"} <= draft_columns
    assert "ix_investment_valuations_book_account_observed" in investment_valuation_indexes
    assert {
        "ledger_books",
        "book_members",
        "transaction_lines",
        "category_versions",
        "budgets",
        "budget_targets",
        "auth_identities",
        "password_accounts",
    } <= tables
    assert any(index[2] for index in posting_indexes)


def test_alembic_adopts_legacy_sqlite_schema_without_destroying_data(tmp_path):
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
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        account_columns = {row[1] for row in connection.execute("pragma table_info(accounts)").fetchall()}
        transaction_columns = {row[1] for row in connection.execute("pragma table_info(transactions)").fetchall()}
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'").fetchall()
        }

    assert version == "0011_posting_position_invariants"
    assert {"institution_type", "subtype", "institution", "book_id"} <= account_columns
    assert "book_id" in transaction_columns
    assert "category_id" not in transaction_columns
    assert {"recurring_items", "ledger_books", "transaction_lines", "auth_identities", "password_accounts"} <= tables


def test_alembic_migrations_are_idempotent_across_restart(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    database_url = f"sqlite:///{database_path}"

    FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute("select version_num from alembic_version").fetchall()

    assert versions == [("0011_posting_position_invariants",)]
