from __future__ import annotations

from sqlalchemy import text


STATUS_COUNT_TABLES = (
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
    "idempotency_receipts",
)


class SystemStatusStorageMixin:
    def database_readiness(self) -> dict[str, str | None]:
        with self.engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                database = self.engine.url.database or "sqlite"
                schema = None
            else:
                database = connection.execute(text("select current_database()")).scalar_one_or_none()
                schema = connection.execute(text("select current_schema()")).scalar_one_or_none()
            revision = connection.execute(text("select version_num from alembic_version")).scalar_one_or_none()
            return {"database": database, "schema": schema, "alembic_revision": revision}

    def status_table_counts(self, table_names: tuple[str, ...] = STATUS_COUNT_TABLES) -> dict[str, int]:
        with self.engine.connect() as connection:
            return {
                table_name: int(connection.execute(text(f'select count(*) from "{table_name}"')).scalar_one())
                for table_name in table_names
            }
