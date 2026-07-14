"""Add deterministic V1 backfill control records."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0008_backfill_control"
down_revision = "v2_0007_monthly_summary"
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
        "backfill_source_receipts",
        sa.Column("snapshot_id", sa.String(80), nullable=False),
        sa.Column("source_table", sa.String(96), nullable=False),
        sa.Column("source_primary_key", sa.Text(), nullable=False),
        sa.Column("canonical_source_key", sa.Text(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=True),
        sa.Column("source_hash", sa.LargeBinary(), nullable=False),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "btrim(snapshot_id) <> ''",
            name=op.f("ck_backfill_source_receipts_snapshot_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(source_table) <> ''",
            name=op.f("ck_backfill_source_receipts_table_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(source_primary_key) <> ''",
            name=op.f("ck_backfill_source_receipts_pk_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(canonical_source_key) <> ''",
            name=op.f("ck_backfill_source_receipts_key_nonblank"),
        ),
        sa.CheckConstraint(
            "octet_length(source_hash) = 32",
            name=op.f("ck_backfill_source_receipts_hash_length"),
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "source_table", "source_primary_key"),
        sa.UniqueConstraint(
            "snapshot_id",
            "source_table",
            "canonical_source_key",
            name="uq_backfill_receipts_canonical_key",
        ),
    )
    op.create_table(
        "backfill_checkpoints",
        sa.Column("snapshot_id", sa.String(80), nullable=False),
        sa.Column("source_table", sa.String(96), nullable=False),
        sa.Column("manifest_hash", sa.LargeBinary(), nullable=False),
        sa.Column("last_canonical_source_key", sa.Text(), nullable=False),
        sa.Column("processed_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(snapshot_id) <> ''",
            name=op.f("ck_backfill_checkpoints_snapshot_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(source_table) <> ''",
            name=op.f("ck_backfill_checkpoints_table_nonblank"),
        ),
        sa.CheckConstraint(
            "octet_length(manifest_hash) = 32",
            name=op.f("ck_backfill_checkpoints_hash_length"),
        ),
        sa.CheckConstraint(
            "btrim(last_canonical_source_key) <> ''",
            name=op.f("ck_backfill_checkpoints_key_nonblank"),
        ),
        sa.CheckConstraint(
            "processed_count > 0", name=op.f("ck_backfill_checkpoints_count_positive")
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "source_table"),
    )
    op.create_table(
        "backfill_quarantine",
        sa.Column("quarantine_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.String(80), nullable=False),
        sa.Column("source_table", sa.String(96), nullable=False),
        sa.Column("source_primary_key", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.String(96), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(snapshot_id) <> ''",
            name=op.f("ck_backfill_quarantine_snapshot_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(source_table) <> ''",
            name=op.f("ck_backfill_quarantine_table_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(source_primary_key) <> ''",
            name=op.f("ck_backfill_quarantine_pk_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(reason_code) <> ''",
            name=op.f("ck_backfill_quarantine_reason_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name=op.f("ck_backfill_quarantine_details_object"),
        ),
        sa.CheckConstraint(
            "decision in ('pending','accepted','skipped')",
            name=op.f("ck_backfill_quarantine_decision_valid"),
        ),
        sa.CheckConstraint(
            "(decision = 'pending' and decided_by is null and decided_at is null) or "
            "(decision <> 'pending' and decided_by is not null and "
            "btrim(decided_by) <> '' and decided_at is not null)",
            name=op.f("ck_backfill_quarantine_decision_shape"),
        ),
        sa.PrimaryKeyConstraint("quarantine_id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "source_table",
            "source_primary_key",
            "reason_code",
            name="uq_backfill_quarantine_source_reason",
        ),
    )
    op.create_table(
        "backfill_seals",
        sa.Column("snapshot_id", sa.String(80), nullable=False),
        sa.Column("manifest_hash", sa.LargeBinary(), nullable=False),
        sa.Column("source_counts", postgresql.JSONB(), nullable=False),
        sa.Column("terminal_book_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("quarantine_count", sa.BigInteger(), nullable=False),
        sa.Column("receipt_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "sealed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(snapshot_id) <> ''", name=op.f("ck_backfill_seals_snapshot_nonblank")
        ),
        sa.CheckConstraint(
            "octet_length(manifest_hash) = 32",
            name=op.f("ck_backfill_seals_hash_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_counts) = 'object'",
            name=op.f("ck_backfill_seals_counts_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(terminal_book_hashes) = 'object'",
            name=op.f("ck_backfill_seals_hashes_object"),
        ),
        sa.CheckConstraint(
            "quarantine_count >= 0",
            name=op.f("ck_backfill_seals_quarantine_nonnegative"),
        ),
        sa.CheckConstraint(
            "receipt_count >= 0", name=op.f("ck_backfill_seals_receipt_nonnegative")
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    connection = op.get_bind()
    for table in (
        "backfill_source_receipts",
        "backfill_checkpoints",
        "backfill_quarantine",
        "backfill_seals",
    ):
        connection.exec_driver_sql(
            f"revoke all privileges on table public.{table} from public, {runtime}"
        )
        connection.exec_driver_sql(
            f"grant select, insert on table public.{table} to {runtime}"
        )
    connection.exec_driver_sql(
        f"grant update (last_canonical_source_key, processed_count, updated_at) "
        f"on public.backfill_checkpoints to {runtime}"
    )
    connection.exec_driver_sql(
        f"grant update (decision, decided_by, decided_at) "
        f"on public.backfill_quarantine to {runtime}"
    )
    connection.exec_driver_sql(
        """
        create function public.v2_guard_backfill_checkpoint()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            if new.snapshot_id is distinct from old.snapshot_id
               or new.source_table is distinct from old.source_table
               or new.manifest_hash is distinct from old.manifest_hash then
                raise exception using errcode = '23514',
                    message = 'backfill checkpoint identity is immutable';
            end if;
            if new.last_canonical_source_key <= old.last_canonical_source_key
               or new.processed_count <= old.processed_count then
                raise exception using errcode = '23514',
                    message = 'backfill checkpoint must advance monotonically';
            end if;
            new.updated_at := clock_timestamp();
            return new;
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        "create trigger trg_backfill_checkpoints_guard "
        "before update on public.backfill_checkpoints for each row "
        "execute function public.v2_guard_backfill_checkpoint()"
    )
    connection.exec_driver_sql(
        """
        create function public.v2_guard_backfill_quarantine()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        begin
            if new.quarantine_id is distinct from old.quarantine_id
               or new.snapshot_id is distinct from old.snapshot_id
               or new.source_table is distinct from old.source_table
               or new.source_primary_key is distinct from old.source_primary_key
               or new.reason_code is distinct from old.reason_code
               or new.details is distinct from old.details then
                raise exception using errcode = '23514',
                    message = 'backfill quarantine evidence is immutable';
            end if;
            if old.decision <> 'pending' then
                if new is distinct from old then
                    raise exception using errcode = '23514',
                        message = 'backfill quarantine decision is terminal';
                end if;
                return new;
            end if;
            if new.decision not in ('accepted', 'skipped')
               or new.decided_by is null or btrim(new.decided_by) = '' then
                raise exception using errcode = '23514',
                    message = 'backfill quarantine decision is invalid';
            end if;
            new.decided_at := clock_timestamp();
            return new;
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        "create trigger trg_backfill_quarantine_guard "
        "before update on public.backfill_quarantine for each row "
        "execute function public.v2_guard_backfill_quarantine()"
    )
    for function_name in (
        "v2_guard_backfill_checkpoint()",
        "v2_guard_backfill_quarantine()",
    ):
        connection.exec_driver_sql(
            f"revoke all privileges on function public.{function_name} "
            f"from public, {runtime}"
        )


def downgrade() -> None:
    raise RuntimeError(
        "the Track Anywhere V2 backfill control migration is irreversible"
    )
