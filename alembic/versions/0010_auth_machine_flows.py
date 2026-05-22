"""add auth machine flow metadata

Revision ID: 0010_auth_machine_flows
Revises: 0009_reversal_investment_links
Create Date: 2026-05-22 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0010_auth_machine_flows"
down_revision: Union[str, Sequence[str], None] = "0009_reversal_investment_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    credential_columns = _columns(inspector, "credentials")
    _add_column("credentials", credential_columns, sa.Column("auth_kind", sa.String(length=40), nullable=False, server_default="api_key"))
    _add_column("credentials", credential_columns, sa.Column("name", sa.String(length=120), nullable=True))
    _add_column("credentials", credential_columns, sa.Column("description", sa.String(length=240), nullable=False, server_default=""))
    _add_column("credentials", credential_columns, sa.Column("key_prefix", sa.String(length=80), nullable=True))
    _add_column("credentials", credential_columns, sa.Column("created_by_actor_id", sa.String(length=80), nullable=True))
    _add_column("credentials", credential_columns, sa.Column("last_used_at", sa.String(length=80), nullable=True))
    _add_column("credentials", credential_columns, sa.Column("rotated_from_jti", sa.String(length=80), nullable=True))

    tables = set(inspector.get_table_names())
    if "oauth_authorization_grants" not in tables:
        op.create_table(
            "oauth_authorization_grants",
            sa.Column("code_hash", sa.String(length=80), nullable=False),
            sa.Column("client_id", sa.String(length=256), nullable=False),
            sa.Column("redirect_uri", sa.String(length=512), nullable=False),
            sa.Column("actor_id", sa.String(length=80), nullable=False),
            sa.Column("actor_type", sa.String(length=40), nullable=False),
            sa.Column("actor_scopes", sa.JSON(), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("code_challenge", sa.String(length=128), nullable=False),
            sa.Column("resource", sa.String(length=512), nullable=True),
            sa.Column("expires_at", sa.String(length=80), nullable=False),
            sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint("code_hash"),
        )
    if "oauth_device_grants" not in tables:
        op.create_table(
            "oauth_device_grants",
            sa.Column("device_code_hash", sa.String(length=80), nullable=False),
            sa.Column("user_code_hash", sa.String(length=80), nullable=False),
            sa.Column("client_id", sa.String(length=256), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("resource", sa.String(length=512), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("expires_at", sa.String(length=80), nullable=False),
            sa.Column("interval_seconds", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(length=80), nullable=False),
            sa.Column("last_poll_at", sa.String(length=80), nullable=True),
            sa.Column("poll_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("approved_actor_id", sa.String(length=80), nullable=True),
            sa.Column("approved_actor_type", sa.String(length=40), nullable=True),
            sa.Column("approved_actor_scopes", sa.JSON(), nullable=True),
            sa.Column("approved_at", sa.String(length=80), nullable=True),
            sa.PrimaryKeyConstraint("device_code_hash"),
        )
        op.create_index("ix_oauth_device_grants_user_code_hash", "oauth_device_grants", ["user_code_hash"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "oauth_device_grants" in tables:
        op.drop_index("ix_oauth_device_grants_user_code_hash", table_name="oauth_device_grants")
        op.drop_table("oauth_device_grants")
    if "oauth_authorization_grants" in tables:
        op.drop_table("oauth_authorization_grants")
    for column in ("rotated_from_jti", "last_used_at", "created_by_actor_id", "key_prefix", "description", "name", "auth_kind"):
        _drop_column_if_exists("credentials", column)


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _add_column(table: str, existing: set[str], column) -> None:
    if column.name not in existing:
        op.add_column(table, column)
        existing.add(column.name)


def _drop_column_if_exists(table: str, column: str) -> None:
    inspector = inspect(op.get_bind())
    if column in _columns(inspector, table):
        op.drop_column(table, column)
