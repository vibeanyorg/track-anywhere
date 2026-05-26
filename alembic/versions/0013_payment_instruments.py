"""payment instrument persistence

Revision ID: 0013_payment_instruments
Revises: 0012_payment_profiles
Create Date: 2026-05-26 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0013_payment_instruments"
down_revision: Union[str, Sequence[str], None] = "0012_payment_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "payment_instruments" in inspector.get_table_names():
        return
    op.create_table(
        "payment_instruments",
        sa.Column("instrument_id", sa.String(length=80), primary_key=True),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("account_id", sa.String(length=80), nullable=False),
        sa.Column("last4", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("book_id", "slug", name="uq_payment_instruments_book_slug"),
    )
    op.create_index("ix_payment_instruments_book_status", "payment_instruments", ["book_id", "status"])
    op.create_index("ix_payment_instruments_account", "payment_instruments", ["account_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "payment_instruments" not in tables:
        return
    op.drop_index("ix_payment_instruments_account", table_name="payment_instruments")
    op.drop_index("ix_payment_instruments_book_status", table_name="payment_instruments")
    op.drop_table("payment_instruments")
