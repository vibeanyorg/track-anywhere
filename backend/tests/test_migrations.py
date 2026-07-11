from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from schema_assertions import (
    COUNTERPARTY_COLUMNS,
    PAYMENT_INSTRUMENT_COLUMNS,
    PAYMENT_PROFILE_COLUMNS,
    index_columns,
)
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


REPO_ROOT = Path(__file__).resolve().parents[2]


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
        counterparty_columns = {row[1] for row in connection.execute("pragma table_info(counterparties)").fetchall()}

    assert version == "0021_attachment_content"
    assert PAYMENT_PROFILE_COLUMNS <= payment_profile_columns
    assert PAYMENT_INSTRUMENT_COLUMNS <= payment_instrument_columns
    assert COUNTERPARTY_COLUMNS <= counterparty_columns
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
        counterparty_indexes = index_columns(connection, "counterparties")

    assert version == "0021_attachment_content"
    assert "accounts" in tables
    assert "auth_identities" in tables
    assert "payment_profiles" in tables
    assert "payment_instruments" in tables
    assert "counterparties" in tables
    assert payment_profile_indexes["ix_payment_profiles_book_status"] == (False, ("book_id", "status"))
    assert counterparty_indexes["ix_counterparties_book_kind_status"] == (
        False,
        ("book_id", "kind", "status"),
    )
    assert (True, ("book_id", "slug")) in payment_profile_indexes.values()
    assert (True, ("book_id", "slug")) in counterparty_indexes.values()
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

    assert version == "0021_attachment_content"
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


def test_alembic_backfills_debit_credit_side_from_legacy_signed_postings(tmp_path):
    database_path = tmp_path / "legacy-posting-side.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_account(connection, "acc_food", type="expense")
        _insert_account(connection, "acc_card", type="liability")
        _insert_transaction(connection, "txn_asset_expense", memo="", purpose="lunch")
        _insert_posting(connection, "txn_asset_expense", 0, "acc_cash", "-38")
        _insert_posting(connection, "txn_asset_expense", 1, "acc_food", "38")
        _insert_transaction(connection, "txn_legacy_liability", memo="", purpose="legacy liability balance")
        _insert_posting(connection, "txn_legacy_liability", 0, "acc_card", "12")
        _insert_posting(connection, "txn_legacy_liability", 1, "acc_cash", "-12")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        postings = connection.execute(
            """
            select transaction_id, account_id, amount, side, amount_semantics
            from postings
            where transaction_id in ('txn_asset_expense', 'txn_legacy_liability')
            order by transaction_id, position
            """
        ).fetchall()

    assert postings == [
        ("txn_asset_expense", "acc_cash", "-38", "credit", "legacy_signed"),
        ("txn_asset_expense", "acc_food", "38", "debit", "legacy_signed"),
        ("txn_legacy_liability", "acc_card", "12", "debit", "legacy_signed"),
        ("txn_legacy_liability", "acc_cash", "-12", "credit", "legacy_signed"),
    ]


def test_legacy_sqlite_adoption_backfills_posting_side_and_future_debit_credit_defaults(tmp_path):
    database_path = tmp_path / "legacy-sqlite-adoption-posting-semantics.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0007_drop_django_tables")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_account(connection, "acc_food", type="expense")
        _insert_transaction(connection, "txn_legacy_adopted", memo="", purpose="legacy adopted")
        _insert_posting(connection, "txn_legacy_adopted", 0, "acc_cash", "-10")
        _insert_posting(connection, "txn_legacy_adopted", 1, "acc_food", "10")
        connection.execute("drop table alembic_version")

    FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    with sqlite3.connect(database_path) as connection:
        _insert_transaction(connection, "txn_new_after_adoption", memo="", purpose="new debit credit")
        connection.execute(
            """
            insert into postings (transaction_id, position, account_id, side, amount, currency)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("txn_new_after_adoption", 0, "acc_food", "debit", "10", "CNY"),
        )
        version = connection.execute("select version_num from alembic_version").fetchone()[0]
        rows = connection.execute(
            """
            select transaction_id, side, amount, amount_semantics
            from postings
            where transaction_id in ('txn_legacy_adopted', 'txn_new_after_adoption')
            order by transaction_id, position
            """
        ).fetchall()

    assert version == "0021_attachment_content"
    assert rows == [
        ("txn_legacy_adopted", "credit", "-10", "legacy_signed"),
        ("txn_legacy_adopted", "debit", "10", "legacy_signed"),
        ("txn_new_after_adoption", "debit", "10", "debit_credit"),
    ]


def test_alembic_defaults_new_postings_to_debit_credit_after_legacy_backfill(tmp_path):
    database_path = tmp_path / "posting-semantics-default.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_account(connection, "acc_food", type="expense")
        _insert_transaction(connection, "txn_legacy", memo="", purpose="legacy")
        _insert_posting(connection, "txn_legacy", 0, "acc_cash", "-10")
        _insert_posting(connection, "txn_legacy", 1, "acc_food", "10")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        _insert_transaction(connection, "txn_new", memo="", purpose="new debit credit")
        connection.execute(
            """
            insert into postings (transaction_id, position, account_id, side, amount, currency)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("txn_new", 0, "acc_food", "debit", "10", "CNY"),
        )
        rows = connection.execute(
            """
            select transaction_id, side, amount, amount_semantics
            from postings
            where transaction_id in ('txn_legacy', 'txn_new')
            order by transaction_id, position
            """
        ).fetchall()

    assert rows == [
        ("txn_legacy", "credit", "-10", "legacy_signed"),
        ("txn_legacy", "debit", "10", "legacy_signed"),
        ("txn_new", "debit", "10", "debit_credit"),
    ]


def test_alembic_defaults_new_draft_postings_to_debit_credit_after_legacy_backfill(tmp_path):
    database_path = tmp_path / "draft-posting-semantics-default.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_draft(connection, "draft_legacy")
        connection.execute(
            """
            insert into draft_postings (draft_id, position, account_id, amount, currency)
            values (?, ?, ?, ?, ?)
            """,
            ("draft_legacy", 0, "acc_food", "10", "CNY"),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        _insert_draft(connection, "draft_new")
        connection.execute(
            """
            insert into draft_postings (draft_id, position, account_id, side, amount, currency)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("draft_new", 0, "acc_food", "debit", "10", "CNY"),
        )
        rows = connection.execute(
            """
            select draft_id, side, amount, amount_semantics
            from draft_postings
            where draft_id in ('draft_legacy', 'draft_new')
            order by draft_id, position
            """
        ).fetchall()

    assert rows == [
        ("draft_legacy", "debit", "10", "legacy_signed"),
        ("draft_new", "debit", "10", "debit_credit"),
    ]


def test_migration_preserves_invalid_legacy_signed_rows_for_audit(tmp_path):
    database_path = tmp_path / "legacy-zero-posting-audit.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_account(connection, "acc_food", type="expense")
        _insert_transaction(connection, "txn_zero_legacy", memo="", purpose="dirty legacy fixture")
        _insert_posting(connection, "txn_zero_legacy", 0, "acc_cash", "0")
        _insert_posting(connection, "txn_zero_legacy", 1, "acc_food", "5")

    command.upgrade(config, "head")

    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)

    audit = service.posting_semantics_audit(service.owner_token)

    assert audit["cutover_ready"] is False
    assert audit["manual_review_blockers"][0]["issue_type"] == "invalid_legacy_signed_shape"


def test_migrated_balance_reads_do_not_treat_unknown_posting_semantics_as_signed_amount(tmp_path):
    database_path = tmp_path / "legacy-invalid-posting-semantics.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_transaction(connection, "txn_unknown_semantics", memo="", purpose="dirty semantic fixture")
        _insert_posting(connection, "txn_unknown_semantics", 0, "acc_cash", "25")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            update postings
            set amount_semantics = 'unknown'
            where transaction_id = 'txn_unknown_semantics'
            """
        )

    service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    service.storage._read_transactions = None

    balance = service.account_balance(service.owner_token, "acc_cash")

    assert balance["official_balance"]["amount"] == "0"


def test_migration_makes_posting_amount_semantics_not_null(tmp_path):
    database_path = tmp_path / "posting-semantics-not-null.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        posting_columns = {
            row[1]: row[3]
            for row in connection.execute("pragma table_info(postings)")
        }
        draft_posting_columns = {
            row[1]: row[3]
            for row in connection.execute("pragma table_info(draft_postings)")
        }

    assert posting_columns["amount_semantics"] == 1
    assert draft_posting_columns["amount_semantics"] == 1


def test_migration_constraints_reject_invalid_debit_credit_shape(tmp_path):
    database_path = tmp_path / "legacy-invalid-debit-credit-shape.sqlite3"
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_account(connection, "acc_cash", type="asset")
        _insert_transaction(connection, "txn_invalid_debit_credit_shape", memo="", purpose="dirty debit credit fixture")
        _insert_posting(connection, "txn_invalid_debit_credit_shape", 0, "acc_cash", "25")

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            update postings
            set amount = '-25',
                side = 'debit',
                amount_semantics = 'debit_credit'
            where transaction_id = 'txn_invalid_debit_credit_shape'
            """
        )


def test_migration_constraints_reject_invalid_draft_debit_credit_shape(tmp_path):
    database_path = tmp_path / "legacy-invalid-draft-debit-credit-shape.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0016_budget_counterparty_targets")

    with sqlite3.connect(database_path) as connection:
        _insert_draft(connection, "draft_invalid_debit_credit_shape")
        connection.execute(
            """
            insert into draft_postings (draft_id, position, account_id, amount, currency)
            values (?, ?, ?, ?, ?)
            """,
            ("draft_invalid_debit_credit_shape", 0, "acc_food", "25", "CNY"),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            update draft_postings
            set amount = '-25',
                side = 'debit',
                amount_semantics = 'debit_credit'
            where draft_id = 'draft_invalid_debit_credit_shape'
            """
        )


def test_posting_semantic_constraint_migration_only_blocks_canonical_debit_credit_shape():
    source = (REPO_ROOT / "alembic/versions/0019_posting_constraints.py").read_text()

    assert "ck_postings_debit_credit_shape" in source
    assert "ck_draft_postings_debit_credit_shape" in source
    assert "ck_postings_amount_semantics" not in source
    assert "ck_draft_postings_amount_semantics" not in source
    assert "ck_postings_legacy_nonzero" not in source
    assert "ck_draft_postings_legacy_nonzero" not in source


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
    existing_columns = {row[1] for row in sqlite_connection.execute("pragma table_info(transactions)").fetchall()}
    columns = ["transaction_id", "book_id", "memo", "occurred_at", "purpose"]
    values = [transaction_id, "book_default", memo, "2026-05-16T12:30:00+08:00", purpose]
    if "category_id" in existing_columns:
        columns.append("category_id")
        values.append(category_id)
    columns.extend(["reversed_by", "version"])
    values.extend([None, 1])
    placeholders = ", ".join("?" for _ in columns)
    sqlite_connection.execute(
        f"""
        insert into transactions ({", ".join(columns)})
        values ({placeholders})
        """,
        values,
    )


def _insert_posting(sqlite_connection, transaction_id: str, position: int, account_id: str, amount: str) -> None:
    sqlite_connection.execute(
        """
        insert into postings (transaction_id, position, account_id, amount, currency)
        values (?, ?, ?, ?, ?)
        """,
        (transaction_id, position, account_id, amount, "CNY"),
    )


def _insert_draft(sqlite_connection, draft_id: str) -> None:
    sqlite_connection.execute(
        """
        insert into drafts (
            draft_id, book_id, memo, state, missing_fields, source, confidence,
            version, attachment_id, category_id, metadata
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (draft_id, "book_default", "", "pending", "[]", "agent", 1.0, 1, None, None, "{}"),
    )


def _insert_transaction_line(
    sqlite_connection,
    line_id: str,
    transaction_id: str,
    *,
    memo: str,
) -> None:
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
