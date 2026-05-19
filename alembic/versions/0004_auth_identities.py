"""auth identities for oauth and rbac login

Revision ID: 0004_auth_identities
Revises: 0003_domain_redesign
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_auth_identities"
down_revision: Union[str, Sequence[str], None] = "0003_domain_redesign"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_identities",
        sa.Column("identity_id", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=240), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("picture_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("identity_id"),
        sa.UniqueConstraint("provider", "subject", name="uq_auth_identity_provider_subject"),
    )


def downgrade() -> None:
    op.drop_table("auth_identities")
