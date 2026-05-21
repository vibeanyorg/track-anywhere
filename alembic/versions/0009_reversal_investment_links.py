"""add asset catalog, reversal links, and investment context

Revision ID: 0009_reversal_investment_links
Revises: 0008_retire_legacy_categories
Create Date: 2026-05-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0009_reversal_investment_links"
down_revision: Union[str, Sequence[str], None] = "0008_retire_legacy_categories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_ASSET_ROWS = [
    {"asset_code": "CNY", "kind": "fiat", "scale": 2, "display_scale": 2, "name": "Chinese yuan", "status": "active", "version": 1},
    {"asset_code": "USD", "kind": "fiat", "scale": 2, "display_scale": 2, "name": "US dollar", "status": "active", "version": 1},
    {"asset_code": "HKD", "kind": "fiat", "scale": 2, "display_scale": 2, "name": "Hong Kong dollar", "status": "active", "version": 1},
    {"asset_code": "EUR", "kind": "fiat", "scale": 2, "display_scale": 2, "name": "Euro", "status": "active", "version": 1},
    {"asset_code": "GBP", "kind": "fiat", "scale": 2, "display_scale": 2, "name": "British pound", "status": "active", "version": 1},
    {"asset_code": "JPY", "kind": "fiat", "scale": 0, "display_scale": 0, "name": "Japanese yen", "status": "active", "version": 1},
    {"asset_code": "KRW", "kind": "fiat", "scale": 0, "display_scale": 0, "name": "South Korean won", "status": "active", "version": 1},
    {"asset_code": "VND", "kind": "fiat", "scale": 0, "display_scale": 0, "name": "Vietnamese dong", "status": "active", "version": 1},
    {"asset_code": "BTC", "kind": "crypto", "scale": 8, "display_scale": 8, "name": "Bitcoin", "status": "active", "version": 1},
    {"asset_code": "ETH", "kind": "crypto", "scale": 18, "display_scale": 8, "name": "Ether", "status": "active", "version": 1},
    {"asset_code": "USDC", "kind": "crypto", "scale": 6, "display_scale": 6, "name": "USD Coin", "status": "active", "version": 1},
    {"asset_code": "USDT", "kind": "crypto", "scale": 6, "display_scale": 6, "name": "Tether USD", "status": "active", "version": 1},
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "assets" not in tables:
        op.create_table(
            "assets",
            sa.Column("asset_code", sa.String(length=16), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("scale", sa.Integer(), nullable=False),
            sa.Column("display_scale", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("asset_code"),
        )
        _seed_assets()
        inspector = inspect(bind)
        tables = set(inspector.get_table_names())
    else:
        asset_columns = _columns(inspector, "assets")
        if "display_scale" not in asset_columns:
            op.add_column("assets", sa.Column("display_scale", sa.Integer(), nullable=False, server_default="2"))
        _seed_assets()

    transaction_columns = _columns(inspector, "transactions")
    if "reverses_transaction_id" not in transaction_columns:
        op.add_column("transactions", sa.Column("reverses_transaction_id", sa.String(length=80), nullable=True))

    posting_columns = _columns(inspector, "postings")
    if "book_id" not in posting_columns:
        op.add_column("postings", sa.Column("book_id", sa.String(length=80), nullable=True))
        op.execute(sa.text("""
            update postings
            set book_id = (
                select transactions.book_id
                from transactions
                where transactions.transaction_id = postings.transaction_id
            )
            where book_id is null
        """))

    investment_columns = _columns(inspector, "investment_events")
    if "transaction_id" not in investment_columns:
        op.add_column("investment_events", sa.Column("transaction_id", sa.String(length=80), nullable=True))

    if "investment_valuations" not in tables:
        op.create_table(
            "investment_valuations",
            sa.Column("valuation_id", sa.String(length=80), nullable=False),
            sa.Column("book_id", sa.String(length=80), nullable=False, server_default="book_default"),
            sa.Column("account_id", sa.String(length=80), nullable=False),
            sa.Column("value", sa.String(length=80), nullable=False),
            sa.Column("currency", sa.String(length=16), nullable=False),
            sa.Column("observed_at", sa.String(length=80), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("memo", sa.String(length=256), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("valuation_id"),
        )
        inspector = inspect(bind)

    _create_index_if_missing(inspector, "ix_transactions_book_occurred", "transactions", ["book_id", "occurred_at", "transaction_id"])
    _create_index_if_missing(inspector, "ix_postings_account_transaction", "postings", ["account_id", "transaction_id"])
    _create_index_if_missing(inspector, "ix_accounts_book_type_currency", "accounts", ["book_id", "type", "currency"])
    _create_index_if_missing(inspector, "ix_transaction_lines_book_category", "transaction_lines", ["book_id", "category_id"])
    _create_index_if_missing(
        inspector,
        "ix_investment_events_book_account_occurred",
        "investment_events",
        ["book_id", "account_id", "occurred_at"],
    )
    _create_index_if_missing(
        inspector,
        "ix_investment_valuations_book_account_observed",
        "investment_valuations",
        ["book_id", "account_id", "observed_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for index_name, table_name in (
        ("ix_investment_valuations_book_account_observed", "investment_valuations"),
        ("ix_investment_events_book_account_occurred", "investment_events"),
        ("ix_transaction_lines_book_category", "transaction_lines"),
        ("ix_accounts_book_type_currency", "accounts"),
        ("ix_postings_account_transaction", "postings"),
        ("ix_transactions_book_occurred", "transactions"),
    ):
        if table_name in inspector.get_table_names() and index_name in {index["name"] for index in inspector.get_indexes(table_name)}:
            op.drop_index(index_name, table_name=table_name)
    if "investment_valuations" in inspector.get_table_names():
        op.drop_table("investment_valuations")
    if bind.dialect.name != "sqlite":
        if "transaction_id" in _columns(inspector, "investment_events"):
            op.drop_column("investment_events", "transaction_id")
        if "book_id" in _columns(inspector, "postings"):
            op.drop_column("postings", "book_id")
        if "reverses_transaction_id" in _columns(inspector, "transactions"):
            op.drop_column("transactions", "reverses_transaction_id")
    if "assets" in inspector.get_table_names():
        op.drop_table("assets")


def _columns(inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _create_index_if_missing(inspector, index_name: str, table_name: str, columns: list[str]) -> None:
    if table_name not in inspector.get_table_names():
        return
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns)


def _seed_assets() -> None:
    asset_table = sa.table(
        "assets",
        sa.column("asset_code", sa.String),
        sa.column("kind", sa.String),
        sa.column("scale", sa.Integer),
        sa.column("display_scale", sa.Integer),
        sa.column("name", sa.String),
        sa.column("status", sa.String),
        sa.column("version", sa.Integer),
    )
    connection = op.get_bind()
    existing = {row[0] for row in connection.execute(sa.text("select asset_code from assets")).fetchall()}
    rows = [row for row in DEFAULT_ASSET_ROWS if row["asset_code"] not in existing]
    if rows:
        op.bulk_insert(asset_table, rows)
