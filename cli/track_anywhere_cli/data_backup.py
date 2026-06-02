from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url

from .config import create_sqlite_backup, database_url_from_env, safe_backup_label
from .posting_semantics import backup_posting_semantics


POSTGRES_BACKUP_COUNT_TABLES = (
    "ledger_books",
    "book_members",
    "accounts",
    "assets",
    "categories",
    "category_versions",
    "transaction_lines",
    "transactions",
    "postings",
    "audit_events",
)


def create_data_backup(
    database_url: str | None = None,
    output_dir: str | None = None,
    label: str | None = None,
    *,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    resolved_database_url = database_url or database_url_from_env()
    url = make_url(resolved_database_url)
    driver = url.drivername.split("+", 1)[0]
    if driver == "sqlite":
        if transaction_id:
            raise RuntimeError("sqlite backup copies the full database; omit --transaction-id")
        return create_sqlite_backup(resolved_database_url, output_dir, label)
    if driver not in {"postgresql", "postgres"}:
        raise RuntimeError("data backup supports sqlite and postgresql databases")
    return create_postgres_backup(resolved_database_url, output_dir, label, transaction_id=transaction_id)


def create_postgres_backup(
    database_url: str,
    output_dir: str | None = None,
    label: str | None = None,
    *,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    if not transaction_id:
        raise RuntimeError("postgresql backup requires --transaction-id for a targeted audit snapshot")
    backup_dir = Path(output_dir).expanduser() if output_dir else Path.cwd() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    backup_path = backup_dir / _postgres_backup_filename(transaction_id, created_at, label)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        database = connection.execute(text("select current_database()")).scalar_one_or_none()
        schema = connection.execute(text("select current_schema()")).scalar_one_or_none()
        alembic_revision = connection.execute(text("select version_num from alembic_version")).scalar_one_or_none()
        transaction_rows = _rows(connection, "select * from transactions where transaction_id = :transaction_id", {"transaction_id": transaction_id})
        if not transaction_rows:
            raise RuntimeError(f"transaction not found: {transaction_id}")
        line_rows = _rows(connection, "select * from transaction_lines where transaction_id = :transaction_id order by position, line_id", {"transaction_id": transaction_id})
        posting_rows = _rows(connection, "select * from postings where transaction_id = :transaction_id order by position, id", {"transaction_id": transaction_id})
        category_ids = sorted({row["category_id"] for row in line_rows if row.get("category_id")})
        category_version_ids = sorted({row["category_version_id"] for row in line_rows if row.get("category_version_id")})
        account_ids = sorted({row["account_id"] for row in posting_rows if row.get("account_id")})
        payload = {
            "schema_version": "postgres-tx-backup.v1",
            "created_at": created_at.isoformat(),
            "database": database,
            "schema": schema,
            "alembic_revision": alembic_revision,
            "database_url": make_url(database_url).render_as_string(hide_password=True),
            "backup_type": "postgres_transaction_json",
            "posting_semantics": backup_posting_semantics(),
            "transaction_id": transaction_id,
            "counts": _counts(connection),
            "transactions": transaction_rows,
            "transaction_lines": line_rows,
            "postings": posting_rows,
            "accounts": _rows_by_ids(connection, "accounts", "account_id", account_ids),
            "categories": _rows_by_ids(connection, "categories", "category_id", category_ids),
            "category_versions": _rows_by_ids(connection, "category_versions", "category_version_id", category_version_ids),
        }

    backup_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "backup_path": str(backup_path),
        "bytes": backup_path.stat().st_size,
        "created_at": created_at.isoformat(),
        "database": database,
        "schema": schema,
        "alembic_revision": alembic_revision,
        "backup_type": "postgres_transaction_json",
        "transaction_id": transaction_id,
    }


def _postgres_backup_filename(transaction_id: str, created_at: datetime, label: str | None) -> str:
    filename_parts = ["postgres", "tx", transaction_id, created_at.strftime("%Y%m%d-%H%M%S")]
    label_part = safe_backup_label(label)
    if label_part:
        filename_parts.append(label_part)
    return "-".join(filename_parts) + ".json"


def _counts(connection) -> dict[str, int]:
    return {
        table_name: int(connection.execute(text(f'select count(*) from "{table_name}"')).scalar_one())
        for table_name in POSTGRES_BACKUP_COUNT_TABLES
    }


def _rows(connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in connection.execute(text(sql), params)]


def _rows_by_ids(connection, table_name: str, id_column: str, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    statement = text(f'select * from "{table_name}" where "{id_column}" in :ids order by "{id_column}"').bindparams(bindparam("ids", expanding=True))
    return [dict(row._mapping) for row in connection.execute(statement, {"ids": ids})]
