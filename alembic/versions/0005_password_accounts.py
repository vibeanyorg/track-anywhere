"""persistent password accounts

Revision ID: 0005_password_accounts
Revises: 0004_auth_identities
Create Date: 2026-05-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_password_accounts"
down_revision: Union[str, Sequence[str], None] = "0004_auth_identities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_accounts",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=150), nullable=False),
        sa.Column("password_hash", sa.String(length=260), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("password_accounts")
