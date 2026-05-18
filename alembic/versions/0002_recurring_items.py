"""recurring subscription items

Revision ID: 0002_recurring
Revises: 0001_initial
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_recurring"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("category_id", sa.String(length=80), nullable=True))
    op.add_column("drafts", sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table(
        "recurring_items",
        sa.Column("recurring_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("recurrence", sa.JSON(), nullable=False),
        sa.Column("reminder_days", sa.JSON(), nullable=False),
        sa.Column("anchor_date", sa.String(length=20), nullable=False),
        sa.Column("source_account_id", sa.String(length=80), nullable=True),
        sa.Column("category_id", sa.String(length=80), nullable=True),
        sa.Column("last_draft_renewal_date", sa.String(length=20), nullable=True),
        sa.Column("last_draft_id", sa.String(length=80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("recurring_id"),
    )


def downgrade() -> None:
    op.drop_table("recurring_items")
    op.drop_column("drafts", "metadata")
    op.drop_column("drafts", "category_id")
