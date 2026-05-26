from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from schema_assertions import PAYMENT_INSTRUMENT_COLUMNS, PAYMENT_PROFILE_COLUMNS, index_columns
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_alembic_clears_legacy_duplicate_transaction_memos(tmp_path):
    database_path = tmp_path / "legacy-duplicate-memo.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0005_password_accounts")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_account(connection, "acc_food", type="expense")
        _insert_transaction(connection, "txn_duplicate", memo="meal", purpose="meal")
        _insert_transaction(connection, "txn_private", memo="card ending 1234", purpose="meal")
        _insert_posting(connection, "txn_duplicate", 0, "acc_cash", "-1")
        _insert_posting(connection, "txn_duplicate", 1, "acc_food", "1")
        _insert_posting(connection, "txn_private", 0, "acc_cash", "-1")
        _insert_posting(connection, "txn_private", 1, "acc_food", "1")
        _insert_transaction_line(connection, "line_duplicate", "txn_duplicate", memo="meal")
        _insert_transaction_line(connection, "line_private", "txn_private", memo="card ending 1234")

    FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    with sqlite3.connect(database_path) as connection:
        transaction_memos = dict(connection.execute("select transaction_id, memo from transactions").fetchall())
        line_memos = dict(connection.execute("select line_id, memo from transaction_lines").fetchall())
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        payment_profile_columns = {row[1] for row in connection.execute("pragma table_info(payment_profiles)").fetchall()}
        payment_instrument_columns = {row[1] for row in connection.execute("pragma table_info(payment_instruments)").fetchall()}

    assert version == "0013_payment_instruments"
    assert PAYMENT_PROFILE_COLUMNS <= payment_profile_columns
    assert PAYMENT_INSTRUMENT_COLUMNS <= payment_instrument_columns
    assert transaction_memos["txn_duplicate"] == ""
    assert transaction_memos["txn_private"] == "card ending 1234"
    assert line_memos["line_duplicate"] == ""
    assert line_memos["line_private"] == "card ending 1234"


def test_alembic_drops_legacy_django_tables_without_dropping_track_anywhere_tables(tmp_path):
    database_path = tmp_path / "legacy-django.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0006_split_memo_purpose")

    with sqlite3.connect(database_path) as connection:
        for table_name in (
            "account_emailaddress",
            "auth_user",
            "auth_user_groups",
            "django_migrations",
            "guardian_userobjectpermission",
            "socialaccount_socialaccount",
        ):
            connection.execute(f"create table {table_name} (id integer primary key)")

    FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        payment_profile_indexes = index_columns(connection, "payment_profiles")

    assert version == "0013_payment_instruments"
    assert "accounts" in tables
    assert "auth_identities" in tables
    assert "payment_profiles" in tables
    assert "payment_instruments" in tables
    assert payment_profile_indexes["ix_payment_profiles_book_status"] == (False, ("book_id", "status"))
    assert (True, ("book_id", "slug")) in payment_profile_indexes.values()
    assert not {
        "account_emailaddress",
        "auth_user",
        "auth_user_groups",
        "django_migrations",
        "guardian_userobjectpermission",
        "socialaccount_socialaccount",
    } & tables


def test_alembic_backfills_lines_and_drops_legacy_category_columns(tmp_path):
    database_path = tmp_path / "legacy-categories.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0007_drop_django_tables")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_account(connection, "acc_expense", type="expense")
        _insert_category(connection, "cat_food", primary="Food")
        _insert_transaction(connection, "txn_food", memo="", purpose="lunch", category_id="cat_food")
        _insert_posting(connection, "txn_food", 0, "acc_cash", "-38")
        _insert_posting(connection, "txn_food", 1, "acc_expense", "38")

    FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        transaction_columns = {row[1] for row in connection.execute("pragma table_info(transactions)").fetchall()}
        category_columns = {row[1] for row in connection.execute("pragma table_info(categories)").fetchall()}
        line = connection.execute(
            """
            select category_id, amount, currency
            from transaction_lines
            where transaction_id = 'txn_food'
            """
        ).fetchone()
        audit = connection.execute(
            """
            select status, legacy_category_id, created_line_id
            from transaction_category_migration_audit
            where transaction_id = 'txn_food'
            """
        ).fetchone()

    assert version == "0013_payment_instruments"
    assert "category_id" not in transaction_columns
    assert "primary" not in category_columns
    assert "secondary" not in category_columns
    assert line == ("cat_food", "38", "CNY")
    assert audit[0] == "created_line"
    assert audit[1] == "cat_food"
    assert audit[2].startswith("line_migrated_")


def test_alembic_category_backfill_preflight_reports_all_blockers(tmp_path):
    database_path = tmp_path / "legacy-category-preflight.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0007_drop_django_tables")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_category(connection, "cat_food", primary="Food")
        _insert_transaction(connection, "txn_missing_category", memo="", purpose="bad", category_id="cat_missing")
        _insert_transaction(connection, "txn_ambiguous_amount", memo="", purpose="bad", category_id="cat_food")
        _insert_posting(connection, "txn_ambiguous_amount", 0, "acc_cash", "-38")

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(config, "head")

    message = str(exc_info.value)
    assert "legacy category cutover preflight failed" in message
    assert "txn_missing_category" in message
    assert "legacy category does not exist" in message
    assert "txn_ambiguous_amount" in message
    assert "cannot derive exactly one reporting line" in message


def _insert_account(sqlite_connection, account_id: str, *, type: str) -> None:
    sqlite_connection.execute(
        """
        insert into accounts (
            account_id, book_id, name, type, currency, institution_type, subtype, institution, version
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (account_id, "book_default", account_id, type, "CNY", None, None, None, 1),
    )

def _insert_category(sqlite_connection, category_id: str, *, primary: str) -> None:
    sqlite_connection.execute(
        """
        insert into categories (
            category_id, book_id, kind, "primary", secondary, parent_id, name, normalized_name,
            level, path_cache, icon, color, sort_order, status, version
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_id,
            "book_default",
            "expense",
            primary,
            None,
            None,
            primary,
            primary.casefold(),
            1,
            primary,
            None,
            None,
            0,
            "active",
            1,
        ),
    )
    sqlite_connection.execute(
        """
        insert into category_versions (
            category_version_id, category_id, book_id, name, parent_id, path, icon, color,
            valid_from, valid_to, change_reason, version
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "catver_food",
            category_id,
            "book_default",
            primary,
            None,
            primary,
            None,
            None,
            "2026-05-21T00:00:00+00:00",
            None,
            "create",
            1,
        ),
    )


def _insert_transaction(
    sqlite_connection,
    transaction_id: str,
    *,
    memo: str,
    purpose: str,
    category_id: str | None = None,
) -> None:
    sqlite_connection.execute(
        """
        insert into transactions (
            transaction_id, book_id, memo, occurred_at, purpose, category_id, reversed_by, version
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (transaction_id, "book_default", memo, "2026-05-16T12:30:00+08:00", purpose, category_id, None, 1),
    )


def _insert_posting(sqlite_connection, transaction_id: str, position: int, account_id: str, amount: str) -> None:
    sqlite_connection.execute(
        """
        insert into postings (transaction_id, position, account_id, amount, currency)
        values (?, ?, ?, ?, ?)
        """,
        (transaction_id, position, account_id, amount, "CNY"),
    )


def _insert_transaction_line(sqlite_connection, line_id: str, transaction_id: str, *, memo: str) -> None:
    sqlite_connection.execute(
        """
        insert into transaction_lines (
            line_id, transaction_id, position, line_type, amount, currency, book_id, category_id,
            category_version_id, category_path_snapshot, merchant_id, project_id, necessity,
            reimbursement_status, memo, version
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            line_id,
            transaction_id,
            0,
            "expense",
            "1",
            "CNY",
            "book_default",
            None,
            None,
            None,
            None,
            None,
            "unknown",
            "none",
            memo,
            1,
        ),
    )
