"""default future postings to debit credit semantics

Revision ID: 0018_debit_credit_defaults
Revises: 0017_posting_debit_credit_side
Create Date: 2026-06-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0018_debit_credit_defaults"
down_revision: Union[str, Sequence[str], None] = "0017_posting_debit_credit_side"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _alter_amount_semantics_default("postings", "debit_credit")
    _alter_amount_semantics_default("draft_postings", "debit_credit")


def downgrade() -> None:
    _alter_amount_semantics_default("draft_postings", "legacy_signed")
    _alter_amount_semantics_default("postings", "legacy_signed")


def _alter_amount_semantics_default(table_name: str, default: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    if "amount_semantics" not in {column["name"] for column in inspector.get_columns(table_name)}:
        return
    _backfill_missing_amount_semantics(table_name)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "amount_semantics",
                existing_type=sa.String(length=20),
                existing_nullable=False,
                nullable=False,
                server_default=default,
            )
        return
    op.alter_column(
        table_name,
        "amount_semantics",
        existing_type=sa.String(length=20),
        existing_nullable=False,
        nullable=False,
        server_default=default,
    )


def _backfill_missing_amount_semantics(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            update {table_name}
            set amount_semantics = 'legacy_signed'
            where amount_semantics is null or amount_semantics = ''
            """
        )
    )
