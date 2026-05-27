"""counterparty catalog

Revision ID: 0014_counterparties
Revises: 0013_payment_instruments
Create Date: 2026-05-27 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0014_counterparties"
down_revision: Union[str, Sequence[str], None] = "0013_payment_instruments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "counterparties" in inspector.get_table_names():
        return
    op.create_table(
        "counterparties",
        sa.Column("counterparty_id", sa.String(length=80), primary_key=True),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("book_id", "slug", name="uq_counterparties_book_slug"),
    )
    op.create_index(
        "ix_counterparties_book_kind_status",
        "counterparties",
        ["book_id", "kind", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "counterparties" not in tables:
        return
    op.drop_index("ix_counterparties_book_kind_status", table_name="counterparties")
    op.drop_table("counterparties")
