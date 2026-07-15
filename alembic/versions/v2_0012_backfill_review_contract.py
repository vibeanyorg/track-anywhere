"""Bind reviewed credit-card semantics to one frozen backfill snapshot."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0012_backfill_review_contract"
down_revision = "v2_0011_reporting_counterparty"
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
    op.create_table(
        "backfill_review_contracts",
        sa.Column("snapshot_id", sa.String(80), nullable=False),
        sa.Column("review_kind", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.LargeBinary(), nullable=False),
        sa.Column("review_hash", sa.LargeBinary(), nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(snapshot_id) <> ''",
            name=op.f("ck_backfill_review_contracts_snapshot_nonblank"),
        ),
        sa.CheckConstraint(
            "review_kind = 'credit_card_semantics_v1'",
            name=op.f("ck_backfill_review_contracts_kind_supported"),
        ),
        sa.CheckConstraint(
            "octet_length(manifest_hash) = 32",
            name=op.f("ck_backfill_review_contracts_manifest_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(review_hash) = 32",
            name=op.f("ck_backfill_review_contracts_review_hash_length"),
        ),
        sa.CheckConstraint(
            "btrim(reviewer) <> ''",
            name=op.f("ck_backfill_review_contracts_reviewer_nonblank"),
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "review_kind"),
    )
    connection = op.get_bind()
    connection.exec_driver_sql(
        "revoke all privileges on table public.backfill_review_contracts "
        f"from public, {runtime}"
    )
    connection.exec_driver_sql(
        "grant select, insert on table public.backfill_review_contracts "
        f"to {runtime}"
    )


def downgrade() -> None:
    raise RuntimeError(
        "the Track Anywhere V2 backfill review contract migration is irreversible"
    )
