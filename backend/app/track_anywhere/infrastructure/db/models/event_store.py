from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


_NOW = text("clock_timestamp()")
_GLOBAL_SEQUENCE_DEFAULT = text("nextval('public.ledger_global_sequence'::regclass)")
_RECEIPT_STATUS = postgresql.ENUM(
    "processing",
    "completed",
    name="receipt_status",
    create_type=False,
)


class BookEventHeadRecord(V2Base):
    __tablename__ = "book_event_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("last_position >= 0", name="last_position_nonnegative"),
        CheckConstraint("octet_length(last_hash) = 32", name="last_hash_length"),
        CheckConstraint(
            "last_position <> 0 or last_hash = decode(repeat('00', 32), 'hex')",
            name="zero_position_zero_hash",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    last_position: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_hash: Mapped[bytes] = mapped_column(LargeBinary)


class LedgerEventRecord(V2Base):
    __tablename__ = "ledger_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "causation_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_ledger_events_book_causation_event",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("book_id", "event_id", name="uq_ledger_events_book_event"),
        UniqueConstraint(
            "book_id", "book_position", name="uq_ledger_events_book_position"
        ),
        UniqueConstraint(
            "book_id",
            "stream_type",
            "stream_id",
            "stream_version",
            name="uq_ledger_events_book_stream_version",
        ),
        UniqueConstraint("book_id", "event_hash", name="uq_ledger_events_book_hash"),
        UniqueConstraint(
            "book_id",
            "stream_type",
            "stream_id",
            "stream_version",
            "book_position",
            "event_id",
            name="uq_ledger_events_stream_head_binding",
        ),
        UniqueConstraint("global_sequence", name="uq_ledger_events_global_sequence"),
        CheckConstraint("global_sequence > 0", name="global_sequence_positive"),
        CheckConstraint("book_position > 0", name="book_position_positive"),
        CheckConstraint("btrim(stream_type) <> ''", name="stream_type_nonblank"),
        CheckConstraint("stream_version > 0", name="stream_version_positive"),
        CheckConstraint("btrim(event_type) <> ''", name="event_type_nonblank"),
        CheckConstraint(
            "event_schema_version > 0", name="event_schema_version_positive"
        ),
        CheckConstraint(
            "btrim(actor_subject_id) <> ''", name="actor_subject_id_nonblank"
        ),
        CheckConstraint(
            "causation_event_id is null or causation_event_id <> event_id",
            name="causation_not_self",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint(
            "octet_length(previous_hash) = 32", name="previous_hash_length"
        ),
        CheckConstraint("octet_length(event_hash) = 32", name="event_hash_length"),
        Index(
            "ix_ledger_events_book_effective",
            "book_id",
            "effective_at",
            "book_position",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    global_sequence: Mapped[int] = mapped_column(
        BigInteger, server_default=_GLOBAL_SEQUENCE_DEFAULT
    )
    book_id: Mapped[UUID] = mapped_column()
    book_position: Mapped[int] = mapped_column(BigInteger)
    stream_type: Mapped[str] = mapped_column(String(32))
    stream_id: Mapped[UUID] = mapped_column()
    stream_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    event_schema_version: Mapped[int] = mapped_column(SmallInteger)
    command_id: Mapped[UUID] = mapped_column()
    actor_subject_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[UUID] = mapped_column()
    causation_event_id: Mapped[UUID | None] = mapped_column(nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    payload: Mapped[dict[str, object]] = mapped_column(postgresql.JSONB)
    previous_hash: Mapped[bytes] = mapped_column(LargeBinary)
    event_hash: Mapped[bytes] = mapped_column(LargeBinary)


class EventStreamHeadRecord(V2Base):
    __tablename__ = "event_stream_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
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
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("btrim(stream_type) <> ''", name="stream_type_nonblank"),
        CheckConstraint("last_version > 0", name="last_version_positive"),
        CheckConstraint("last_book_position > 0", name="last_book_position_positive"),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    stream_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    stream_id: Mapped[UUID] = mapped_column(primary_key=True)
    last_version: Mapped[int] = mapped_column(Integer)
    last_book_position: Mapped[int] = mapped_column(BigInteger)
    last_event_id: Mapped[UUID] = mapped_column()


class CommandReceiptRecord(V2Base):
    __tablename__ = "command_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "first_book_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_command_receipts_first_event",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "last_book_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_command_receipts_last_event",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("command_id", name="uq_command_receipts_command_id"),
        CheckConstraint(
            "btrim(actor_subject_id) <> ''", name="actor_subject_id_nonblank"
        ),
        CheckConstraint("btrim(operation) <> ''", name="operation_nonblank"),
        CheckConstraint(
            "octet_length(idempotency_key_hash) = 32",
            name="idempotency_key_hash_length",
        ),
        CheckConstraint("octet_length(request_hash) = 32", name="request_hash_length"),
        CheckConstraint(
            "(first_book_position is null) = (last_book_position is null) "
            "and (first_book_position is null "
            "or (first_book_position > 0 and "
            "last_book_position >= first_book_position))",
            name="book_position_pair",
        ),
        CheckConstraint(
            "(status = 'processing' "
            "and response_schema_version is null "
            "and result_status is null "
            "and result_body is null "
            "and first_book_position is null "
            "and last_book_position is null "
            "and completed_at is null) "
            "or (status = 'completed' "
            "and response_schema_version is not null "
            "and response_schema_version > 0 "
            "and result_status is not null "
            "and result_status between 100 and 599 "
            "and result_body is not null "
            "and completed_at is not null)",
            name="lifecycle_shape",
        ),
    )

    actor_subject_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(String(96), primary_key=True)
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary)
    command_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(_RECEIPT_STATUS)
    response_schema_version: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    result_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    result_body: Mapped[dict[str, object] | list[object] | None] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    first_book_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_book_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "BookEventHeadRecord",
    "CommandReceiptRecord",
    "EventStreamHeadRecord",
    "LedgerEventRecord",
]
