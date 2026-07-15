"""Preserve opaque historical counterparty references on reporting lines."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0011_reporting_counterparty"
down_revision = "v2_0010_credit_card_transactions"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not _IDENTIFIER.fullmatch(value) or len(value.encode("ascii")) > 63:
        raise RuntimeError(
            "TRACK_ANYWHERE_DB_RUNTIME_ROLE is required and must be safe"
        )
    return f'"{value}"'


def upgrade() -> None:
    runtime = _runtime_role()
    op.add_column(
        "reporting_lines",
        sa.Column("counterparty_id", sa.Uuid(), nullable=True),
    )
    op.get_bind().exec_driver_sql(
        f"grant insert (counterparty_id) on table public.reporting_lines to {runtime}"
    )


def downgrade() -> None:
    raise RuntimeError(
        "the Track Anywhere V2 reporting counterparty migration is irreversible"
    )
