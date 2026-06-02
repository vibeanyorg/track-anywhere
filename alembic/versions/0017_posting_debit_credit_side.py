"""add debit credit posting side

Revision ID: 0017_posting_debit_credit_side
Revises: 0016_budget_counterparty_targets
Create Date: 2026-05-31 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0017_posting_debit_credit_side"
down_revision: Union[str, Sequence[str], None] = "0016_budget_counterparty_targets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "postings" in tables:
        _add_side_column_if_missing("postings", inspector)
        _add_amount_semantics_column_if_missing("postings", inspector)
        _backfill_side("postings")
        _backfill_amount_semantics("postings")
    if "draft_postings" in tables:
        _add_side_column_if_missing("draft_postings", inspector)
        _add_amount_semantics_column_if_missing("draft_postings", inspector)
        _backfill_side("draft_postings")
        _backfill_amount_semantics("draft_postings")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "draft_postings" in tables and _column_exists(inspector, "draft_postings", "amount_semantics"):
        _drop_column("draft_postings", "amount_semantics")
    if "draft_postings" in tables and _column_exists(inspector, "draft_postings", "side"):
        _drop_column("draft_postings", "side")
    if "postings" in tables and _column_exists(inspector, "postings", "amount_semantics"):
        _drop_column("postings", "amount_semantics")
    if "postings" in tables and _column_exists(inspector, "postings", "side"):
        _drop_column("postings", "side")


def _add_side_column_if_missing(table_name: str, inspector) -> None:
    if _column_exists(inspector, table_name, "side"):
        return
    op.add_column(table_name, sa.Column("side", sa.String(length=10), nullable=True))


def _add_amount_semantics_column_if_missing(table_name: str, inspector) -> None:
    if _column_exists(inspector, table_name, "amount_semantics"):
        return
    op.add_column(
        table_name,
        sa.Column(
            "amount_semantics",
            sa.String(length=20),
            nullable=False,
            server_default="legacy_signed",
        ),
    )


def _drop_column(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column(column_name)
    else:
        op.drop_column(table_name, column_name)


def _column_exists(inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _backfill_side(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            update {table_name}
            set side = case
                when cast(amount as numeric) > 0 then 'debit'
                else 'credit'
            end
            where side is null
            """
        )
    )


def _backfill_amount_semantics(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            update {table_name}
            set amount_semantics = 'legacy_signed'
            where amount_semantics is null or amount_semantics = ''
            """
        )
    )
