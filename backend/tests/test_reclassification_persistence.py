from __future__ import annotations

import sqlite3

from sqlalchemy import event

from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def _seed_expense(tmp_path):
    database_path = tmp_path / "track-anywhere.sqlite3"
    service = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    token = service.owner_token
    cash, _ = service.create_account(
        token,
        {"name": "Reclassify Cash", "type": "asset", "currency": "CNY", "opening_balance": "100"},
        idempotency_key="reclassify-cash",
    )
    food, _ = service.create_category(token, {"kind": "expense", "name": "Food"}, idempotency_key="food")
    dining, _ = service.create_category(token, {"kind": "expense", "name": "Dining"}, idempotency_key="dining")
    transaction, _ = service.record_expense(
        token,
        {
            "amount": "38",
            "currency": "CNY",
            "from_account_id": cash.account_id,
            "category_id": food.category_id,
            "purpose": "lunch",
        },
        idempotency_key="lunch",
    )
    return database_path, service, token, transaction, dining


def test_reclassification_uses_database_line_not_dirty_memory(tmp_path):
    database_path, service, token, transaction, dining = _seed_expense(tmp_path)
    line_id = transaction.lines[0].line_id
    transaction.lines[0].line_type = "income"
    transaction.lines[0].category_id = "cat_dirty_memory"

    updated, replay = service.reclassify_transaction(
        token,
        {"transaction_id": transaction.transaction_id, "line_id": line_id, "category_id": dining.category_id},
        idempotency_key="reclassify-db-truth",
    )

    assert replay is False
    assert updated.lines[0].line_id == line_id
    assert updated.lines[0].category_id == dining.category_id
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            select line_id, line_type, category_id
            from transaction_lines
            where transaction_id = ?
            order by position, line_id
            """,
            (transaction.transaction_id,),
        ).fetchall()
        posting_count = connection.execute(
            "select count(*) from postings where transaction_id = ?",
            (transaction.transaction_id,),
        ).fetchone()[0]

    assert rows == [(line_id, "expense", dining.category_id)]
    assert posting_count == 2


def test_reclassification_persists_annotation_without_core_rewrite(tmp_path):
    _, service, token, transaction, dining = _seed_expense(tmp_path)
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.lower().split()))

    event.listen(service.storage.engine, "before_cursor_execute", capture_statement)
    try:
        service.reclassify_transaction(
            token,
            {"transaction_id": transaction.transaction_id, "category_id": dining.category_id},
            idempotency_key="reclassify-annotation-only",
        )
    finally:
        event.remove(service.storage.engine, "before_cursor_execute", capture_statement)

    assert not any(statement.startswith("insert into transactions") for statement in statements)
    assert not any(statement.startswith("update transactions") for statement in statements)
    assert not any(statement.startswith("delete from postings") for statement in statements)
    assert not any(statement.startswith("insert into postings") for statement in statements)
    assert not any(statement.startswith("delete from transaction_lines") for statement in statements)


def test_reclassification_updates_in_process_reporting_cache(tmp_path):
    _, service, token, transaction, dining = _seed_expense(tmp_path)

    service.reclassify_transaction(
        token,
        {"transaction_id": transaction.transaction_id, "category_id": dining.category_id},
        idempotency_key="reclassify-report-cache",
    )
    summary = service.category_summary(token, kind="expense", currency="CNY")

    assert summary["groups"] == [
        {
            "category_id": dining.category_id,
            "kind": "expense",
            "primary": "Dining",
            "secondary": None,
            "currency": "CNY",
            "amount": "38",
            "transaction_count": 1,
            "transaction_ids": [transaction.transaction_id],
        }
    ]


def test_reclassification_replay_after_restart_does_not_duplicate_events(tmp_path):
    database_path, service, token, transaction, dining = _seed_expense(tmp_path)
    payload = {"transaction_id": transaction.transaction_id, "category_id": dining.category_id}

    service.reclassify_transaction(token, payload, idempotency_key="reclassify-once")
    restarted = FinanceService(DeploymentSecurityConfig(), database_url=f"sqlite:///{database_path}")
    _, replay = restarted.reclassify_transaction(token, payload, idempotency_key="reclassify-once")

    assert replay is True
    with sqlite3.connect(database_path) as connection:
        classification_events = connection.execute(
            "select count(*) from classification_events where event_type = 'reclassify'"
        ).fetchone()[0]
        audit_events = connection.execute(
            "select count(*) from audit_events where operation = 'ledger.transaction.reclassify'"
        ).fetchone()[0]
        replay_count = connection.execute(
            """
            select replay_count from idempotency_receipts
            where operation = 'ledger.transaction.reclassify'
            """
        ).fetchone()[0]

    assert classification_events == 1
    assert audit_events == 1
    assert replay_count == 1
