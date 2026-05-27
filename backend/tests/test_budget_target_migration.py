from __future__ import annotations

import sqlite3

from alembic import command
from alembic.config import Config


def test_alembic_migrates_budget_targets_from_merchant_to_counterparty(tmp_path):
    database_path = tmp_path / "budget-target-counterparty.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0015_tx_line_counterparties")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            insert into budgets (
                budget_id, book_id, name, period, currency, total_amount, starts_on, ends_on,
                rollover_policy, alert_thresholds, status, version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("budget_1", "book_default", "Budget", "monthly", "CNY", "300", None, None, "none", "[]", "active", 1),
        )
        connection.execute(
            """
            insert into budget_targets (
                budget_target_id, budget_id, target_type, target_id, mode, amount, metadata, version
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("btgt_1", "budget_1", "merchant", "cp_didi", "include", None, "{}", 1),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        target_type = connection.execute(
            "select target_type from budget_targets where budget_target_id = ?",
            ("btgt_1",),
        ).fetchone()[0]
        version = connection.execute("select version_num from alembic_version").fetchone()[0]

    assert version == "0016_budget_counterparty_targets"
    assert target_type == "counterparty"
