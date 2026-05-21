from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import MetaData


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = PROJECT_ROOT / "alembic"


LEGACY_SQLITE_COLUMNS = {
    "accounts": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
        "institution_type": "VARCHAR(40)",
        "subtype": "VARCHAR(64)",
        "institution": "VARCHAR(120)",
    },
    "transactions": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
    },
    "drafts": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
        "category_id": "VARCHAR(80)",
        "metadata": "JSON NOT NULL DEFAULT '{}'",
    },
    "funds": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
    },
    "recurring_items": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
    },
    "investment_events": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
    },
    "categories": {
        "book_id": "VARCHAR(80) NOT NULL DEFAULT 'book_default'",
        "parent_id": "VARCHAR(80)",
        "name": "VARCHAR(80) NOT NULL DEFAULT ''",
        "normalized_name": "VARCHAR(80) NOT NULL DEFAULT ''",
        "level": "INTEGER NOT NULL DEFAULT 1",
        "path_cache": "VARCHAR(180) NOT NULL DEFAULT ''",
        "icon": "VARCHAR(80)",
        "color": "VARCHAR(32)",
        "sort_order": "INTEGER NOT NULL DEFAULT 0",
        "status": "VARCHAR(40) NOT NULL DEFAULT 'active'",
    },
}


def run_migrations(engine: Engine, metadata: MetaData | None = None) -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if metadata is not None and _needs_legacy_sqlite_adoption(connection):
            metadata.create_all(connection)
            _apply_legacy_sqlite_columns(connection)
            command.stamp(config, "0007_drop_django_tables")
        command.upgrade(config, "head")


def _needs_legacy_sqlite_adoption(connection) -> bool:
    if connection.dialect.name != "sqlite":
        return False
    tables = set(inspect(connection).get_table_names())
    if not tables or "alembic_version" in tables:
        return False
    return any(table in tables for table in LEGACY_SQLITE_COLUMNS)


def _apply_legacy_sqlite_columns(connection) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table_name, columns in LEGACY_SQLITE_COLUMNS.items():
        if table_name not in tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
