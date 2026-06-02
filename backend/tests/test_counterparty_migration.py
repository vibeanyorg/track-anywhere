from __future__ import annotations

import sqlite3

from alembic import command
from alembic.config import Config

from schema_assertions import TRANSACTION_LINE_COLUMNS, index_columns


def test_alembic_migrates_transaction_line_merchant_to_counterparty(tmp_path):
    database_path = tmp_path / "line-counterparty.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0014_counterparties")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            insert into accounts (
                account_id, book_id, name, type, currency, institution_type, subtype, institution, version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("acc_cash", "book_default", "Cash", "asset", "CNY", None, None, None, 1),
        )
        connection.execute(
            """
            insert into transactions (
                transaction_id, book_id, memo, occurred_at, purpose, reversed_by, version
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("txn_counterparty", "book_default", "", "2026-05-16T12:30:00+08:00", "coffee", None, 1),
        )
        connection.execute(
            """
            insert into transaction_lines (
                line_id, transaction_id, position, line_type, amount, currency, book_id, category_id,
                category_version_id, category_path_snapshot, merchant_id, project_id, necessity,
                reimbursement_status, memo, version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "line_counterparty",
                "txn_counterparty",
                0,
                "expense",
                "1",
                "CNY",
                "book_default",
                None,
                None,
                None,
                "cp_starbucks",
                None,
                "unknown",
                "none",
                "coffee",
                1,
            ),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("pragma table_info(transaction_lines)").fetchall()}
        indexes = index_columns(connection, "transaction_lines")
        row = connection.execute(
            "select counterparty_id, memo from transaction_lines where line_id = ?",
            ("line_counterparty",),
        ).fetchone()
        version = connection.execute("select version_num from alembic_version").fetchone()[0]

    assert version == "0019_posting_constraints"
    assert TRANSACTION_LINE_COLUMNS <= columns
    assert "merchant_id" not in columns
    assert indexes["ix_transaction_lines_book_counterparty"] == (False, ("book_id", "counterparty_id"))
    assert row == ("cp_starbucks", "coffee")
