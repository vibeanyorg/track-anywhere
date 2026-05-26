"""payment profile persistence

Revision ID: 0012_payment_profiles
Revises: 0011_posting_position_invariants
Create Date: 2026-05-26 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0012_payment_profiles"
down_revision: Union[str, Sequence[str], None] = "0011_posting_position_invariants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNIQUE_CONSTRAINT = "uq_payment_profiles_book_slug"
INDEX_NAME = "ix_payment_profiles_book_status"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "payment_profiles" in inspector.get_table_names():
        return
    op.create_table(
        "payment_profiles",
        sa.Column("profile_id", sa.String(length=80), primary_key=True),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("instrument_account_id", sa.String(length=80), nullable=False),
        sa.Column("instrument_currency", sa.String(length=16), nullable=False),
        sa.Column("backing_account_id", sa.String(length=80), nullable=False),
        sa.Column("backing_currency", sa.String(length=16), nullable=False),
        sa.Column("settlement_mode", sa.String(length=40), nullable=False),
        sa.Column("settlement_rate", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("book_id", "slug", name=UNIQUE_CONSTRAINT),
        sa.Index(INDEX_NAME, "book_id", "status"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "payment_profiles" not in tables:
        return
    op.drop_table("payment_profiles")
