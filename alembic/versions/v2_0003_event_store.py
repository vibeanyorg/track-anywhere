"""Add the immutable V2 event store and command receipts."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0003_event_store"
down_revision = "v2_0002_core_catalog"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_EVENT_INSERT_COLUMNS = (
    "event_id",
    "book_id",
    "book_position",
    "stream_type",
    "stream_id",
    "stream_version",
    "event_type",
    "event_schema_version",
    "command_id",
    "actor_subject_id",
    "correlation_id",
    "causation_event_id",
    "effective_at",
    "payload",
    "previous_hash",
    "event_hash",
)
_TABLE_COLUMN_PRIVILEGES = {
    "book_event_heads": {
        "insert": ("book_id", "last_position", "last_hash"),
        "update": ("last_position", "last_hash"),
    },
    "event_stream_heads": {
        "insert": (
            "book_id",
            "stream_type",
            "stream_id",
            "last_version",
            "last_book_position",
            "last_event_id",
        ),
        "update": ("last_version", "last_book_position", "last_event_id"),
    },
    "command_receipts": {
        "insert": (
            "actor_subject_id",
            "book_id",
            "operation",
            "idempotency_key_hash",
            "request_hash",
            "command_id",
            "status",
        ),
        "update": (
            "status",
            "response_schema_version",
            "result_status",
            "result_body",
            "first_book_position",
            "last_book_position",
            "completed_at",
        ),
    },
}
_RECEIPT_STATUS = postgresql.ENUM(
    "processing",
    "completed",
    name="receipt_status",
    create_type=False,
)


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


def _create_tables() -> None:
    receipt_status = postgresql.ENUM("processing", "completed", name="receipt_status")
    receipt_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "book_event_heads",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column(
            "last_position",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "last_position >= 0",
            name=op.f("ck_book_event_heads_last_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "octet_length(last_hash) = 32",
            name=op.f("ck_book_event_heads_last_hash_length"),
        ),
        sa.CheckConstraint(
            "last_position <> 0 or last_hash = decode(repeat('00', 32), 'hex')",
            name=op.f("ck_book_event_heads_zero_position_zero_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_book_event_heads_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("book_id", name=op.f("pk_book_event_heads")),
    )

    op.execute(
        "create sequence public.ledger_global_sequence "
        "as bigint minvalue 1 start with 1 increment by 1 no cycle"
    )
    op.create_table(
        "ledger_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "global_sequence",
            sa.BigInteger(),
            server_default=sa.text(
                "nextval('public.ledger_global_sequence'::regclass)"
            ),
            nullable=False,
        ),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("book_position", sa.BigInteger(), nullable=False),
        sa.Column("stream_type", sa.String(length=32), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("stream_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("actor_subject_id", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_event_id", sa.Uuid(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("previous_hash", sa.LargeBinary(), nullable=False),
        sa.Column("event_hash", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "global_sequence > 0",
            name=op.f("ck_ledger_events_global_sequence_positive"),
        ),
        sa.CheckConstraint(
            "book_position > 0",
            name=op.f("ck_ledger_events_book_position_positive"),
        ),
        sa.CheckConstraint(
            "btrim(stream_type) <> ''",
            name=op.f("ck_ledger_events_stream_type_nonblank"),
        ),
        sa.CheckConstraint(
            "stream_version > 0",
            name=op.f("ck_ledger_events_stream_version_positive"),
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name=op.f("ck_ledger_events_event_type_nonblank"),
        ),
        sa.CheckConstraint(
            "event_schema_version > 0",
            name=op.f("ck_ledger_events_event_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "btrim(actor_subject_id) <> ''",
            name=op.f("ck_ledger_events_actor_subject_id_nonblank"),
        ),
        sa.CheckConstraint(
            "causation_event_id is null or causation_event_id <> event_id",
            name=op.f("ck_ledger_events_causation_not_self"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name=op.f("ck_ledger_events_payload_object"),
        ),
        sa.CheckConstraint(
            "octet_length(previous_hash) = 32",
            name=op.f("ck_ledger_events_previous_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(event_hash) = 32",
            name=op.f("ck_ledger_events_event_hash_length"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_ledger_events_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_ledger_events")),
        sa.UniqueConstraint("book_id", "event_id", name="uq_ledger_events_book_event"),
        sa.UniqueConstraint(
            "book_id", "book_position", name="uq_ledger_events_book_position"
        ),
        sa.UniqueConstraint(
            "book_id",
            "stream_type",
            "stream_id",
            "stream_version",
            name="uq_ledger_events_book_stream_version",
        ),
        sa.UniqueConstraint("book_id", "event_hash", name="uq_ledger_events_book_hash"),
        sa.UniqueConstraint(
            "book_id",
            "stream_type",
            "stream_id",
            "stream_version",
            "book_position",
            "event_id",
            name="uq_ledger_events_stream_head_binding",
        ),
        sa.UniqueConstraint("global_sequence", name="uq_ledger_events_global_sequence"),
    )
    op.create_foreign_key(
        "fk_ledger_events_book_causation_event",
        "ledger_events",
        "ledger_events",
        ["book_id", "causation_event_id"],
        ["book_id", "event_id"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ledger_events_book_effective",
        "ledger_events",
        ["book_id", "effective_at", "book_position"],
        unique=False,
    )
    op.execute(
        "alter sequence public.ledger_global_sequence "
        "owned by public.ledger_events.global_sequence"
    )

    op.create_table(
        "event_stream_heads",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("stream_type", sa.String(length=32), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("last_version", sa.Integer(), nullable=False),
        sa.Column("last_book_position", sa.BigInteger(), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "btrim(stream_type) <> ''",
            name=op.f("ck_event_stream_heads_stream_type_nonblank"),
        ),
        sa.CheckConstraint(
            "last_version > 0",
            name=op.f("ck_event_stream_heads_last_version_positive"),
        ),
        sa.CheckConstraint(
            "last_book_position > 0",
            name=op.f("ck_event_stream_heads_last_book_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_event_stream_heads_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "book_id",
                "stream_type",
                "stream_id",
                "last_version",
                "last_book_position",
                "last_event_id",
            ],
            [
                "ledger_events.book_id",
                "ledger_events.stream_type",
                "ledger_events.stream_id",
                "ledger_events.stream_version",
                "ledger_events.book_position",
                "ledger_events.event_id",
            ],
            name="fk_event_stream_heads_terminal_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "stream_type",
            "stream_id",
            name=op.f("pk_event_stream_heads"),
        ),
    )

    op.create_table(
        "command_receipts",
        sa.Column("actor_subject_id", sa.String(length=128), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=96), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("status", _RECEIPT_STATUS, nullable=False),
        sa.Column("response_schema_version", sa.SmallInteger(), nullable=True),
        sa.Column("result_status", sa.SmallInteger(), nullable=True),
        sa.Column(
            "result_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("first_book_position", sa.BigInteger(), nullable=True),
        sa.Column("last_book_position", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "btrim(actor_subject_id) <> ''",
            name=op.f("ck_command_receipts_actor_subject_id_nonblank"),
        ),
        sa.CheckConstraint(
            "btrim(operation) <> ''",
            name=op.f("ck_command_receipts_operation_nonblank"),
        ),
        sa.CheckConstraint(
            "octet_length(idempotency_key_hash) = 32",
            name=op.f("ck_command_receipts_idempotency_key_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(request_hash) = 32",
            name=op.f("ck_command_receipts_request_hash_length"),
        ),
        sa.CheckConstraint(
            "(first_book_position is null) = (last_book_position is null) "
            "and (first_book_position is null "
            "or (first_book_position > 0 and "
            "last_book_position >= first_book_position))",
            name=op.f("ck_command_receipts_book_position_pair"),
        ),
        sa.CheckConstraint(
            "(status = 'processing' "
            "and response_schema_version is null "
            "and result_status is null "
            "and result_body is null "
            "and first_book_position is null "
            "and last_book_position is null "
            "and completed_at is null) "
            "or (status = 'completed' "
            "and response_schema_version > 0 "
            "and result_status between 100 and 599 "
            "and result_body is not null "
            "and completed_at is not null)",
            name=op.f("ck_command_receipts_lifecycle_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name=op.f("fk_command_receipts_book_id_books"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "first_book_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_command_receipts_first_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "last_book_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_command_receipts_last_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "actor_subject_id",
            "book_id",
            "operation",
            "idempotency_key_hash",
            name=op.f("pk_command_receipts"),
        ),
        sa.UniqueConstraint("command_id", name="uq_command_receipts_command_id"),
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


def _create_triggers(runtime_role: str) -> None:
    quoted_runtime = _quote_identifier(runtime_role)
    functions = {
        "v2_guard_book_event_head": """
            if tg_op = 'UPDATE' then
                if new.book_id is distinct from old.book_id then
                    raise exception using errcode = '23514',
                        message = 'book event head identity is immutable';
                end if;
                if new.last_position <= old.last_position then
                    raise exception using errcode = '23514',
                        message = 'book event head position must advance';
                end if;
            end if;
            if new.last_position > 0 then
                perform 1
                  from public.ledger_events
                 where book_id = new.book_id
                   and book_position = new.last_position
                   and event_hash = new.last_hash;
                if not found then
                    raise exception using errcode = '23514',
                        message = 'book event head must bind its terminal event';
                end if;
            end if;
            return new;
        """,
        "v2_guard_event_stream_head": """
            if tg_op = 'UPDATE' then
                if new.book_id is distinct from old.book_id
                   or new.stream_type is distinct from old.stream_type
                   or new.stream_id is distinct from old.stream_id then
                    raise exception using errcode = '23514',
                        message = 'event stream head identity is immutable';
                end if;
                if new.last_version <= old.last_version
                   or new.last_book_position <= old.last_book_position then
                    raise exception using errcode = '23514',
                        message = 'event stream head must advance';
                end if;
            end if;
            return new;
        """,
        "v2_reject_ledger_event_mutation": """
            raise exception using errcode = '23514',
                message = 'ledger events are immutable';
        """,
        "v2_guard_command_receipt_lifecycle": """
            if tg_op = 'INSERT' then
                if new.status <> 'processing' then
                    raise exception using errcode = '23514',
                        message = 'command receipts must start processing';
                end if;
                return new;
            end if;
            if new.actor_subject_id is distinct from old.actor_subject_id
               or new.book_id is distinct from old.book_id
               or new.operation is distinct from old.operation
               or new.idempotency_key_hash is distinct from old.idempotency_key_hash
               or new.request_hash is distinct from old.request_hash
               or new.command_id is distinct from old.command_id
               or new.created_at is distinct from old.created_at then
                raise exception using errcode = '23514',
                    message = 'command receipt scope is immutable';
            end if;
            if old.status <> 'processing' or new.status <> 'completed' then
                raise exception using errcode = '23514',
                    message = 'command receipt completion is terminal';
            end if;
            return new;
        """,
        "v2_reject_processing_receipt": """
            if exists (
                select 1
                  from public.command_receipts
                 where actor_subject_id = new.actor_subject_id
                   and book_id = new.book_id
                   and operation = new.operation
                   and idempotency_key_hash = new.idempotency_key_hash
                   and status = 'processing'
            ) then
                raise exception using errcode = '23514',
                    message = 'processing command receipt cannot commit';
            end if;
            return null;
        """,
    }
    for function_name, body in functions.items():
        _create_trigger_function(function_name, body, quoted_runtime)

    connection = op.get_bind()
    connection.exec_driver_sql(
        "create trigger trg_book_event_heads_guard "
        "before insert or update on public.book_event_heads "
        "for each row execute function public.v2_guard_book_event_head()"
    )
    connection.exec_driver_sql(
        "create trigger trg_event_stream_heads_guard "
        "before insert or update on public.event_stream_heads "
        "for each row execute function public.v2_guard_event_stream_head()"
    )
    connection.exec_driver_sql(
        "create trigger trg_ledger_events_immutable "
        "before update or delete on public.ledger_events "
        "for each row execute function public.v2_reject_ledger_event_mutation()"
    )
    connection.exec_driver_sql(
        "create trigger trg_command_receipts_lifecycle "
        "before insert or update on public.command_receipts "
        "for each row execute function "
        "public.v2_guard_command_receipt_lifecycle()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_command_receipts_no_processing_commit "
        "after insert or update on public.command_receipts "
        "deferrable initially deferred for each row execute function "
        "public.v2_reject_processing_receipt()"
    )


def _grant_columns(
    quoted_runtime: str,
    table_name: str,
    privilege: str,
    columns: tuple[str, ...],
) -> None:
    rendered_columns = ", ".join(columns)
    op.get_bind().exec_driver_sql(
        f"grant {privilege} ({rendered_columns}) "
        f"on table public.{table_name} to {quoted_runtime}"
    )


def _apply_runtime_acl(runtime_role: str) -> None:
    connection = op.get_bind()
    quoted_runtime = _quote_identifier(runtime_role)
    table_names = (
        "book_event_heads",
        "event_stream_heads",
        "ledger_events",
        "command_receipts",
    )
    for table_name in table_names:
        connection.exec_driver_sql(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {quoted_runtime}"
        )
        connection.exec_driver_sql(
            f"grant select on table public.{table_name} to {quoted_runtime}"
        )

    _grant_columns(quoted_runtime, "ledger_events", "insert", _EVENT_INSERT_COLUMNS)
    for table_name, privileges in _TABLE_COLUMN_PRIVILEGES.items():
        for privilege, columns in privileges.items():
            _grant_columns(quoted_runtime, table_name, privilege, columns)

    connection.exec_driver_sql(
        "revoke all privileges on sequence public.ledger_global_sequence "
        f"from public, {quoted_runtime}"
    )
    connection.exec_driver_sql(
        f"grant usage on sequence public.ledger_global_sequence to {quoted_runtime}"
    )
    connection.exec_driver_sql(
        f"revoke all privileges on type public.receipt_status "
        f"from public, {quoted_runtime}"
    )
    connection.exec_driver_sql(
        f"grant usage on type public.receipt_status to {quoted_runtime}"
    )


def upgrade() -> None:
    runtime_role = _runtime_role()
    _create_tables()
    _create_triggers(runtime_role)
    _apply_runtime_acl(runtime_role)


def downgrade() -> None:
    raise RuntimeError("the Track Anywhere V2 event-store migration is irreversible")
