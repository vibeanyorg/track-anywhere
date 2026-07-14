"""Add async projection checkpoints and transactional outbox."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0005_async_projection_outbox"
down_revision = "v2_0004_sync_projections"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_TABLES = (
    "projection_checkpoints",
    "projection_generations",
    "projection_dirty_periods",
    "projection_failures",
    "outbox_messages",
)
_INSERT_COLUMNS = {
    "projection_checkpoints": (
        "projection_name",
        "projector_version",
        "book_id",
        "last_book_position",
        "active_generation",
        "lease_owner",
        "lease_expires_at",
    ),
    "projection_generations": (
        "projection_name",
        "projector_version",
        "book_id",
        "generation",
        "state",
        "rebuild_from_position",
        "last_book_position",
        "target_book_position",
    ),
    "projection_dirty_periods": (
        "projection_name",
        "projector_version",
        "book_id",
        "generation",
        "period_start",
        "period_end",
        "source_event_id",
        "source_book_position",
    ),
    "projection_failures": (
        "failure_id",
        "projection_name",
        "projector_version",
        "book_id",
        "generation",
        "source_event_id",
        "source_book_position",
        "event_type",
        "event_schema_version",
        "failure_kind",
        "retry_state",
        "attempt_count",
        "next_retry_at",
        "last_error_code",
    ),
    "outbox_messages": (
        "message_id",
        "book_id",
        "source_event_id",
        "topic",
        "message_type",
        "dedupe_key",
        "payload",
        "available_at",
    ),
}
_UPDATE_COLUMNS = {
    "projection_checkpoints": (
        "last_book_position",
        "active_generation",
        "lease_owner",
        "lease_expires_at",
    ),
    "projection_generations": (
        "state",
        "last_book_position",
        "target_book_position",
    ),
    "projection_dirty_periods": ("source_event_id", "source_book_position"),
    "projection_failures": (
        "retry_state",
        "attempt_count",
        "next_retry_at",
        "last_error_code",
        "resolved_at",
    ),
    "outbox_messages": (
        "attempt_count",
        "available_at",
        "locked_by",
        "locked_until",
        "delivered_at",
        "last_error_code",
    ),
}
_DELETE_TABLES = ("projection_dirty_periods",)


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier) or len(identifier.encode("ascii")) > 63:
        raise RuntimeError(
            "database runtime role must be a safe lowercase PostgreSQL identifier"
        )
    return f'"{identifier}"'


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not value:
        raise RuntimeError("TRACK_ANYWHERE_DB_RUNTIME_ROLE is required")
    _quote_identifier(value)
    return value


def _grant_columns(
    quoted_runtime: str, table_name: str, privilege: str, columns: tuple[str, ...]
) -> None:
    op.get_bind().exec_driver_sql(
        f"grant {privilege} ({', '.join(columns)}) on table public.{table_name} to {quoted_runtime}"
    )


def _create_trigger_function(
    function_name: str, body: str, quoted_runtime: str
) -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        f"""
        create function public.{function_name}()
        returns trigger
        language plpgsql
        security invoker
        set search_path = pg_catalog, public
        as $function$
        begin
            {body}
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        f"revoke all privileges on function public.{function_name}() "
        f"from public, {quoted_runtime}"
    )


def _create_tables() -> None:
    now = sa.text("clock_timestamp()")
    op.create_table(
        "projection_checkpoints",
        sa.Column("projection_name", sa.String(96), nullable=False),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column(
            "last_book_position", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "active_generation", sa.BigInteger(), nullable=False, server_default="1"
        ),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.CheckConstraint(
            "btrim(projection_name) <> ''",
            name=op.f("ck_projection_checkpoints_projection_name_nonblank"),
        ),
        sa.CheckConstraint(
            "projector_version > 0",
            name=op.f("ck_projection_checkpoints_projector_version_positive"),
        ),
        sa.CheckConstraint(
            "last_book_position >= 0",
            name=op.f("ck_projection_checkpoints_last_book_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "active_generation > 0",
            name=op.f("ck_projection_checkpoints_active_generation_positive"),
        ),
        sa.CheckConstraint(
            "(lease_owner is null and lease_expires_at is null) or (lease_owner is not null and btrim(lease_owner) <> '' and lease_expires_at is not null)",
            name=op.f("ck_projection_checkpoints_lease_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="RESTRICT", onupdate="RESTRICT"
        ),
        sa.PrimaryKeyConstraint(
            "projection_name",
            "projector_version",
            "book_id",
            name=op.f("pk_projection_checkpoints"),
        ),
    )
    op.create_table(
        "projection_generations",
        sa.Column("projection_name", sa.String(96), nullable=False),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "rebuild_from_position", sa.BigInteger(), nullable=False, server_default="1"
        ),
        sa.Column(
            "last_book_position", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("target_book_position", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "btrim(projection_name) <> ''",
            name=op.f("ck_projection_generations_projection_name_nonblank"),
        ),
        sa.CheckConstraint(
            "projector_version > 0",
            name=op.f("ck_projection_generations_projector_version_positive"),
        ),
        sa.CheckConstraint(
            "generation > 0", name=op.f("ck_projection_generations_generation_positive")
        ),
        sa.CheckConstraint(
            "state in ('building','catching_up','active','retired','failed')",
            name=op.f("ck_projection_generations_state_valid"),
        ),
        sa.CheckConstraint(
            "rebuild_from_position > 0",
            name=op.f("ck_projection_generations_rebuild_from_position_positive"),
        ),
        sa.CheckConstraint(
            "last_book_position >= 0",
            name=op.f("ck_projection_generations_last_book_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "target_book_position >= 0",
            name=op.f("ck_projection_generations_target_book_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "last_book_position <= target_book_position",
            name=op.f("ck_projection_generations_progress_within_target"),
        ),
        sa.CheckConstraint(
            "target_book_position >= rebuild_from_position - 1",
            name=op.f("ck_projection_generations_target_covers_rebuild_start"),
        ),
        sa.CheckConstraint(
            "(state in ('building','catching_up') and activated_at is null and completed_at is null) or (state = 'active' and activated_at is not null and completed_at is null) or (state = 'retired' and activated_at is not null and completed_at is not null) or (state = 'failed' and activated_at is null and completed_at is not null)",
            name=op.f("ck_projection_generations_lifecycle_timestamps"),
        ),
        sa.CheckConstraint(
            "activated_at is null or activated_at >= created_at",
            name=op.f("ck_projection_generations_activated_after_created"),
        ),
        sa.CheckConstraint(
            "completed_at is null or completed_at >= coalesce(activated_at, created_at)",
            name=op.f("ck_projection_generations_completed_after_start"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="RESTRICT", onupdate="RESTRICT"
        ),
        sa.PrimaryKeyConstraint(
            "projection_name",
            "projector_version",
            "book_id",
            "generation",
            name=op.f("pk_projection_generations"),
        ),
    )
    op.create_index(
        "ux_projection_generations_one_active",
        "projection_generations",
        ["projection_name", "projector_version", "book_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_foreign_key(
        "fk_projection_checkpoints_active_generation",
        "projection_checkpoints",
        "projection_generations",
        ["projection_name", "projector_version", "book_id", "active_generation"],
        ["projection_name", "projector_version", "book_id", "generation"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "projection_dirty_periods",
        sa.Column("projection_name", sa.String(96), nullable=False),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_book_position", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.CheckConstraint(
            "btrim(projection_name) <> ''",
            name=op.f("ck_projection_dirty_periods_projection_name_nonblank"),
        ),
        sa.CheckConstraint(
            "projector_version > 0",
            name=op.f("ck_projection_dirty_periods_projector_version_positive"),
        ),
        sa.CheckConstraint(
            "generation > 0",
            name=op.f("ck_projection_dirty_periods_generation_positive"),
        ),
        sa.CheckConstraint(
            "period_start < period_end",
            name=op.f("ck_projection_dirty_periods_period_range_valid"),
        ),
        sa.CheckConstraint(
            "source_book_position > 0",
            name=op.f("ck_projection_dirty_periods_source_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["projection_name", "projector_version", "book_id", "generation"],
            [
                "projection_generations.projection_name",
                "projection_generations.projector_version",
                "projection_generations.book_id",
                "projection_generations.generation",
            ],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "projection_name",
            "projector_version",
            "book_id",
            "generation",
            "period_start",
            "period_end",
            name=op.f("pk_projection_dirty_periods"),
        ),
    )
    op.create_table(
        "projection_failures",
        sa.Column("failure_id", sa.Uuid(), nullable=False),
        sa.Column("projection_name", sa.String(96), nullable=False),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_book_position", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("failure_kind", sa.String(32), nullable=False),
        sa.Column(
            "retry_state", sa.String(16), nullable=False, server_default="paused"
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "btrim(projection_name) <> ''",
            name=op.f("ck_projection_failures_projection_name_nonblank"),
        ),
        sa.CheckConstraint(
            "projector_version > 0",
            name=op.f("ck_projection_failures_projector_version_positive"),
        ),
        sa.CheckConstraint(
            "source_book_position > 0",
            name=op.f("ck_projection_failures_source_position_positive"),
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name=op.f("ck_projection_failures_event_type_nonblank"),
        ),
        sa.CheckConstraint(
            "event_schema_version > 0",
            name=op.f("ck_projection_failures_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "failure_kind in ('unknown_event','apply_error','constraint_error')",
            name=op.f("ck_projection_failures_failure_kind_valid"),
        ),
        sa.CheckConstraint(
            "retry_state in ('paused','ready','retrying','resolved','dead')",
            name=op.f("ck_projection_failures_retry_state_valid"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_projection_failures_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "btrim(last_error_code) <> ''",
            name=op.f("ck_projection_failures_error_code_nonblank"),
        ),
        sa.CheckConstraint(
            "(retry_state = 'ready') = (next_retry_at is not null)",
            name=op.f("ck_projection_failures_ready_has_next_retry"),
        ),
        sa.CheckConstraint(
            "(retry_state = 'resolved') = (resolved_at is not null)",
            name=op.f("ck_projection_failures_resolved_state_matches_time"),
        ),
        sa.ForeignKeyConstraint(
            ["projection_name", "projector_version", "book_id", "generation"],
            [
                "projection_generations.projection_name",
                "projection_generations.projector_version",
                "projection_generations.book_id",
                "projection_generations.generation",
            ],
            name="fk_projection_failures_generation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("failure_id", name=op.f("pk_projection_failures")),
        sa.UniqueConstraint(
            "projection_name",
            "projector_version",
            "book_id",
            "generation",
            "source_event_id",
            name="uq_projection_failures_source",
        ),
    )
    op.create_table(
        "outbox_messages",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(96), nullable=False),
        sa.Column("message_type", sa.String(96), nullable=False),
        sa.Column("dedupe_key", sa.String(160), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=now
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now,
        ),
        sa.Column("locked_by", sa.String(128), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "btrim(topic) <> ''", name=op.f("ck_outbox_messages_topic_nonblank")
        ),
        sa.CheckConstraint(
            "btrim(message_type) <> ''",
            name=op.f("ck_outbox_messages_message_type_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(dedupe_key) <> ''",
            name=op.f("ck_outbox_messages_dedupe_key_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name=op.f("ck_outbox_messages_payload_object"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_outbox_messages_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "(locked_by is null and locked_until is null) or (locked_by is not null and btrim(locked_by) <> '' and locked_until is not null)",
            name=op.f("ck_outbox_messages_lock_complete"),
        ),
        sa.CheckConstraint(
            "locked_by is null or attempt_count > 0",
            name=op.f("ck_outbox_messages_lock_requires_attempt"),
        ),
        sa.CheckConstraint(
            "delivered_at is null or (locked_by is null and locked_until is null)",
            name=op.f("ck_outbox_messages_delivered_has_no_lock"),
        ),
        sa.CheckConstraint(
            "last_error_code is null or btrim(last_error_code) <> ''",
            name=op.f("ck_outbox_messages_error_code_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_outbox_messages_source_event",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_outbox_messages")),
        sa.UniqueConstraint(
            "book_id",
            "topic",
            "dedupe_key",
            name="uq_outbox_messages_book_topic_dedupe",
        ),
    )
    op.create_index(
        "ix_outbox_messages_available",
        "outbox_messages",
        ["available_at", "message_id"],
        unique=False,
        postgresql_where=sa.text("delivered_at is null"),
    )


def _create_triggers(runtime_role: str) -> None:
    quoted_runtime = _quote_identifier(runtime_role)
    functions = {
        "v2_touch_projection_checkpoint": """
            if tg_op = 'UPDATE' then
                if new.last_book_position < old.last_book_position then
                    raise exception using errcode = '23514',
                        message = 'projection checkpoint cannot move backward';
                end if;
                new.updated_at := clock_timestamp();
            end if;
            return new;
        """,
        "v2_validate_active_projection_generation": """
            declare
                target_projection_name varchar(96);
                target_projector_version integer;
                target_book_id uuid;
                active_count bigint;
                checkpoint_record public.projection_checkpoints%%rowtype;
                active_record public.projection_generations%%rowtype;
            begin
                if tg_op = 'DELETE' then
                    target_projection_name := old.projection_name;
                    target_projector_version := old.projector_version;
                    target_book_id := old.book_id;
                else
                    target_projection_name := new.projection_name;
                    target_projector_version := new.projector_version;
                    target_book_id := new.book_id;
                end if;

                select * into checkpoint_record
                  from public.projection_checkpoints checkpoint
                 where checkpoint.projection_name = target_projection_name
                   and checkpoint.projector_version = target_projector_version
                   and checkpoint.book_id = target_book_id;

                select count(*) into active_count
                  from public.projection_generations generation_record
                 where generation_record.projection_name = target_projection_name
                   and generation_record.projector_version = target_projector_version
                   and generation_record.book_id = target_book_id
                   and generation_record.state = 'active';

                if checkpoint_record.projection_name is null then
                    if active_count <> 0 then
                        raise exception using errcode = '23514',
                            message = 'active generation requires a checkpoint';
                    end if;
                    return null;
                end if;

                if active_count <> 1 then
                    raise exception using errcode = '23514',
                        message = 'checkpoint requires exactly one active generation';
                end if;

                select * into active_record
                  from public.projection_generations generation_record
                 where generation_record.projection_name = target_projection_name
                   and generation_record.projector_version = target_projector_version
                   and generation_record.book_id = target_book_id
                   and generation_record.state = 'active';

                if checkpoint_record.active_generation <> active_record.generation
                   or checkpoint_record.last_book_position <> active_record.last_book_position then
                    raise exception using errcode = '23514',
                        message = 'checkpoint cursor must match the active generation';
                end if;

                if checkpoint_record.last_book_position > 0 and not exists (
                    select 1 from public.ledger_events event_record
                     where event_record.book_id = checkpoint_record.book_id
                       and event_record.book_position = checkpoint_record.last_book_position
                ) then
                    raise exception using errcode = '23514',
                        message = 'checkpoint cursor must reference an existing Book event';
                end if;
                return null;
            end;
        """,
        "v2_validate_projection_generation_update": """
            if tg_op = 'INSERT' then
                if new.state <> 'building' then
                    raise exception using errcode = '23514',
                        message = 'projection generation must start in building state';
                end if;
                new.activated_at := null;
                new.completed_at := null;
            else
                if new.last_book_position < old.last_book_position then
                    raise exception using errcode = '23514',
                        message = 'projection generation progress cannot move backward';
                end if;
                if new.target_book_position < old.target_book_position then
                    raise exception using errcode = '23514',
                        message = 'projection generation target cannot move backward';
                end if;
                if old.state = 'building' then
                    if new.state = 'building' then
                        new.activated_at := null;
                        new.completed_at := null;
                    elsif new.state = 'catching_up' then
                        new.activated_at := null;
                        new.completed_at := null;
                    elsif new.state = 'failed' then
                        new.activated_at := null;
                        new.completed_at := clock_timestamp();
                    else
                        raise exception using errcode = '23514',
                            message = 'invalid projection generation state transition';
                    end if;
                elsif old.state = 'catching_up' then
                    if new.state = 'catching_up' then
                        new.activated_at := null;
                        new.completed_at := null;
                    elsif new.state = 'active' then
                        if new.last_book_position <> new.target_book_position then
                            raise exception using errcode = '23514',
                                message = 'active projection generation must reach its target';
                        end if;
                        new.activated_at := clock_timestamp();
                        new.completed_at := null;
                    elsif new.state = 'failed' then
                        new.activated_at := null;
                        new.completed_at := clock_timestamp();
                    else
                        raise exception using errcode = '23514',
                            message = 'invalid projection generation state transition';
                    end if;
                elsif old.state = 'active' then
                    if new.state = 'active' then
                        new.activated_at := old.activated_at;
                        new.completed_at := null;
                    elsif new.state = 'retired' then
                        new.activated_at := old.activated_at;
                        new.completed_at := clock_timestamp();
                    else
                        raise exception using errcode = '23514',
                            message = 'invalid projection generation state transition';
                    end if;
                elsif old.state in ('retired','failed') then
                    raise exception using errcode = '23514',
                        message = 'terminal projection generation cannot change';
                end if;
            end if;
            if new.last_book_position > 0 and not exists (
                select 1 from public.ledger_events event_record
                 where event_record.book_id = new.book_id
                   and event_record.book_position = new.last_book_position
            ) then
                raise exception using errcode = '23514',
                    message = 'projection generation cursor must reference an existing Book event';
            end if;
            if new.target_book_position > 0 and not exists (
                select 1 from public.ledger_events event_record
                 where event_record.book_id = new.book_id
                   and event_record.book_position = new.target_book_position
            ) then
                raise exception using errcode = '23514',
                    message = 'projection generation target must reference an existing Book event';
            end if;
            return new;
        """,
        "v2_validate_dirty_period_source": """
            if tg_op = 'UPDATE' and new.source_book_position < old.source_book_position then
                raise exception using errcode = '23514',
                    message = 'dirty period source position cannot move backward';
            end if;
            perform 1
              from public.ledger_events event_record
             where event_record.book_id = new.book_id
               and event_record.event_id = new.source_event_id
               and event_record.book_position = new.source_book_position;
            if not found then
                raise exception using errcode = '23514',
                    message = 'dirty period must bind its exact source event';
            end if;
            return new;
        """,
        "v2_validate_projection_failure": """
            if tg_op = 'UPDATE' and new.attempt_count < old.attempt_count then
                raise exception using errcode = '23514',
                    message = 'projection failure attempts cannot move backward';
            end if;
            perform 1
              from public.ledger_events event_record
             where event_record.book_id = new.book_id
               and event_record.event_id = new.source_event_id
               and event_record.book_position = new.source_book_position
               and event_record.event_type = new.event_type
               and event_record.event_schema_version = new.event_schema_version;
            if not found then
                raise exception using errcode = '23514',
                    message = 'projection failure must bind its exact source event';
            end if;
            return new;
        """,
        "v2_validate_outbox_update": """
            if new.attempt_count < old.attempt_count then
                raise exception using errcode = '23514',
                    message = 'outbox attempts cannot move backward';
            end if;
            if old.delivered_at is not null then
                if new.delivered_at is distinct from old.delivered_at
                   or new.attempt_count is distinct from old.attempt_count
                   or new.available_at is distinct from old.available_at
                   or new.locked_by is distinct from old.locked_by
                   or new.locked_until is distinct from old.locked_until
                   or new.last_error_code is distinct from old.last_error_code then
                    raise exception using errcode = '23514',
                        message = 'delivered outbox message is terminal';
                end if;
                return new;
            end if;
            if old.locked_by is distinct from new.locked_by and new.locked_by is not null then
                if new.attempt_count <= old.attempt_count then
                    raise exception using errcode = '23514',
                        message = 'outbox claim owner change must increment attempts';
                end if;
                if old.locked_by is not null and old.locked_until > clock_timestamp() then
                    raise exception using errcode = '23514',
                        message = 'unexpired outbox claim cannot be stolen';
                end if;
            end if;
            if old.delivered_at is null
               and new.delivered_at is null
               and old.locked_by is not null
               and old.locked_until > clock_timestamp()
               and (
                   new.locked_by is null
                   or new.locked_until is null
                   or new.locked_until < old.locked_until
               ) then
                raise exception using errcode = '23514',
                    message = 'unexpired outbox claim cannot be shortened or released';
            end if;
            if old.delivered_at is null and new.delivered_at is not null then
                if old.locked_by is null or old.locked_until is null then
                    raise exception using errcode = '23514',
                        message = 'outbox delivery requires an active claim';
                end if;
                if old.locked_until <= clock_timestamp() then
                    raise exception using errcode = '23514',
                        message = 'outbox delivery requires an unexpired claim';
                end if;
                if old.attempt_count <= 0 then
                    raise exception using errcode = '23514',
                        message = 'outbox delivery requires a positive attempt count';
                end if;
                if new.locked_by is not null or new.locked_until is not null then
                    raise exception using errcode = '23514',
                        message = 'outbox delivery must clear its claim';
                end if;
                if new.last_error_code is not null then
                    raise exception using errcode = '23514',
                        message = 'outbox delivery must clear last error';
                end if;
            end if;
            return new;
        """,
    }
    for function_name, body in functions.items():
        _create_trigger_function(function_name, body, quoted_runtime)

    connection = op.get_bind()
    connection.exec_driver_sql(
        "create trigger trg_projection_checkpoints_touch "
        "before update on public.projection_checkpoints "
        "for each row execute function public.v2_touch_projection_checkpoint()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_projection_checkpoints_active_commit "
        "after insert or update or delete on public.projection_checkpoints "
        "deferrable initially deferred for each row execute function "
        "public.v2_validate_active_projection_generation()"
    )
    connection.exec_driver_sql(
        "create trigger trg_projection_generations_progress "
        "before insert or update on public.projection_generations "
        "for each row execute function public.v2_validate_projection_generation_update()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_projection_generations_active_commit "
        "after insert or update or delete on public.projection_generations "
        "deferrable initially deferred for each row execute function "
        "public.v2_validate_active_projection_generation()"
    )
    connection.exec_driver_sql(
        "create trigger trg_projection_dirty_periods_source "
        "before insert or update on public.projection_dirty_periods "
        "for each row execute function public.v2_validate_dirty_period_source()"
    )
    connection.exec_driver_sql(
        "create trigger trg_projection_failures_source "
        "before insert or update on public.projection_failures "
        "for each row execute function public.v2_validate_projection_failure()"
    )
    connection.exec_driver_sql(
        "create trigger trg_outbox_messages_update "
        "before update on public.outbox_messages "
        "for each row execute function public.v2_validate_outbox_update()"
    )


def _apply_runtime_acl(runtime_role: str) -> None:
    connection = op.get_bind()
    quoted_runtime = _quote_identifier(runtime_role)
    for table in _TABLES:
        connection.exec_driver_sql(
            f"revoke all privileges on table public.{table} from public, {quoted_runtime}"
        )
        connection.exec_driver_sql(
            f"grant select on table public.{table} to {quoted_runtime}"
        )
    for table, cols in _INSERT_COLUMNS.items():
        _grant_columns(quoted_runtime, table, "insert", cols)
    for table, cols in _UPDATE_COLUMNS.items():
        _grant_columns(quoted_runtime, table, "update", cols)
    for table in _DELETE_TABLES:
        connection.exec_driver_sql(
            f"grant delete on table public.{table} to {quoted_runtime}"
        )


def upgrade() -> None:
    runtime_role = _runtime_role()
    _create_tables()
    _create_triggers(runtime_role)
    _apply_runtime_acl(runtime_role)


def downgrade() -> None:
    raise RuntimeError(
        "the Track Anywhere V2 async projection/outbox migration is irreversible"
    )
