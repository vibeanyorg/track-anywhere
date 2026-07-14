from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base

_NOW = text("clock_timestamp()")


class ProjectionCheckpointRecord(V2Base):
    __tablename__ = "projection_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="RESTRICT", onupdate="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["projection_name", "projector_version", "book_id", "active_generation"],
            [
                "projection_generations.projection_name",
                "projection_generations.projector_version",
                "projection_generations.book_id",
                "projection_generations.generation",
            ],
            name="fk_projection_checkpoints_active_generation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "btrim(projection_name) <> ''", name="projection_name_nonblank"
        ),
        CheckConstraint("projector_version > 0", name="projector_version_positive"),
        CheckConstraint(
            "last_book_position >= 0", name="last_book_position_nonnegative"
        ),
        CheckConstraint("active_generation > 0", name="active_generation_positive"),
        CheckConstraint(
            "(lease_owner is null and lease_expires_at is null) or (lease_owner is not null and btrim(lease_owner) <> '' and lease_expires_at is not null)",
            name="lease_complete",
        ),
    )
    projection_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    projector_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    last_book_position: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    active_generation: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class ProjectionGenerationRecord(V2Base):
    __tablename__ = "projection_generations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"], ["books.book_id"], ondelete="RESTRICT", onupdate="RESTRICT"
        ),
        CheckConstraint(
            "btrim(projection_name) <> ''", name="projection_name_nonblank"
        ),
        CheckConstraint("projector_version > 0", name="projector_version_positive"),
        CheckConstraint("generation > 0", name="generation_positive"),
        CheckConstraint(
            "state in ('building','catching_up','active','retired','failed')",
            name="state_valid",
        ),
        CheckConstraint(
            "rebuild_from_position > 0", name="rebuild_from_position_positive"
        ),
        CheckConstraint(
            "last_book_position >= 0", name="last_book_position_nonnegative"
        ),
        CheckConstraint(
            "target_book_position >= 0", name="target_book_position_nonnegative"
        ),
        CheckConstraint(
            "last_book_position <= target_book_position", name="progress_within_target"
        ),
        CheckConstraint(
            "target_book_position >= rebuild_from_position - 1",
            name="target_covers_rebuild_start",
        ),
        CheckConstraint(
            "(state in ('building','catching_up') and activated_at is null and completed_at is null) or (state = 'active' and activated_at is not null and completed_at is null) or (state = 'retired' and activated_at is not null and completed_at is not null) or (state = 'failed' and activated_at is null and completed_at is not null)",
            name="lifecycle_timestamps",
        ),
        CheckConstraint(
            "activated_at is null or activated_at >= created_at",
            name="activated_after_created",
        ),
        CheckConstraint(
            "completed_at is null or completed_at >= coalesce(activated_at, created_at)",
            name="completed_after_start",
        ),
        Index(
            "ux_projection_generations_one_active",
            "projection_name",
            "projector_version",
            "book_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )
    projection_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    projector_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    rebuild_from_position: Mapped[int] = mapped_column(
        BigInteger, server_default=text("1")
    )
    last_book_position: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    target_book_position: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectionDirtyPeriodRecord(V2Base):
    __tablename__ = "projection_dirty_periods"
    __table_args__ = (
        ForeignKeyConstraint(
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
        CheckConstraint(
            "btrim(projection_name) <> ''", name="projection_name_nonblank"
        ),
        CheckConstraint("projector_version > 0", name="projector_version_positive"),
        CheckConstraint("generation > 0", name="generation_positive"),
        CheckConstraint("period_start < period_end", name="period_range_valid"),
        CheckConstraint("source_book_position > 0", name="source_position_positive"),
    )
    projection_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    projector_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    source_event_id: Mapped[UUID] = mapped_column()
    source_book_position: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class ProjectionFailureRecord(V2Base):
    __tablename__ = "projection_failures"
    __table_args__ = (
        ForeignKeyConstraint(
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
        UniqueConstraint(
            "projection_name",
            "projector_version",
            "book_id",
            "generation",
            "source_event_id",
            name="uq_projection_failures_source",
        ),
        CheckConstraint(
            "btrim(projection_name) <> ''", name="projection_name_nonblank"
        ),
        CheckConstraint("projector_version > 0", name="projector_version_positive"),
        CheckConstraint("source_book_position > 0", name="source_position_positive"),
        CheckConstraint("btrim(event_type) <> ''", name="event_type_nonblank"),
        CheckConstraint("event_schema_version > 0", name="schema_version_positive"),
        CheckConstraint(
            "failure_kind in ('unknown_event','apply_error','constraint_error')",
            name="failure_kind_valid",
        ),
        CheckConstraint(
            "retry_state in ('paused','ready','retrying','resolved','dead')",
            name="retry_state_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("btrim(last_error_code) <> ''", name="error_code_nonblank"),
        CheckConstraint(
            "(retry_state = 'ready') = (next_retry_at is not null)",
            name="ready_has_next_retry",
        ),
        CheckConstraint(
            "(retry_state = 'resolved') = (resolved_at is not null)",
            name="resolved_state_matches_time",
        ),
    )
    failure_id: Mapped[UUID] = mapped_column(primary_key=True)
    projection_name: Mapped[str] = mapped_column(String(96))
    projector_version: Mapped[int] = mapped_column(Integer)
    book_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger)
    source_event_id: Mapped[UUID] = mapped_column()
    source_book_position: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(64))
    event_schema_version: Mapped[int] = mapped_column(SmallInteger)
    failure_kind: Mapped[str] = mapped_column(String(32))
    retry_state: Mapped[str] = mapped_column(
        String(16), server_default=text("'paused'")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
