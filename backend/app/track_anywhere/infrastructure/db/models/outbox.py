from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base

_NOW = text("clock_timestamp()")


class OutboxMessageRecord(V2Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_outbox_messages_source_event",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "topic",
            "dedupe_key",
            name="uq_outbox_messages_book_topic_dedupe",
        ),
        CheckConstraint("btrim(topic) <> ''", name="topic_nonblank"),
        CheckConstraint("btrim(message_type) <> ''", name="message_type_nonblank"),
        CheckConstraint("btrim(dedupe_key) <> ''", name="dedupe_key_nonblank"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(locked_by is null and locked_until is null) or (locked_by is not null and btrim(locked_by) <> '' and locked_until is not null)",
            name="lock_complete",
        ),
        CheckConstraint(
            "locked_by is null or attempt_count > 0",
            name="lock_requires_attempt",
        ),
        CheckConstraint(
            "delivered_at is null or (locked_by is null and locked_until is null)",
            name="delivered_has_no_lock",
        ),
        CheckConstraint(
            "last_error_code is null or btrim(last_error_code) <> ''",
            name="error_code_nonblank",
        ),
        Index(
            "ix_outbox_messages_available",
            "available_at",
            "message_id",
            postgresql_where=text("delivered_at is null"),
        ),
    )
    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    book_id: Mapped[UUID] = mapped_column()
    source_event_id: Mapped[UUID] = mapped_column()
    topic: Mapped[str] = mapped_column(String(96))
    message_type: Mapped[str] = mapped_column(String(96))
    dedupe_key: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, object]] = mapped_column(postgresql.JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
