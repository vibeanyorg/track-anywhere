from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class BackfillSourceReceiptRecord(V2Base):
    __tablename__ = "backfill_source_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "snapshot_id",
            "source_table",
            "canonical_source_key",
            name="uq_backfill_receipts_canonical_key",
        ),
        CheckConstraint("btrim(snapshot_id) <> ''", name="snapshot_nonblank"),
        CheckConstraint("btrim(source_table) <> ''", name="table_nonblank"),
        CheckConstraint("btrim(source_primary_key) <> ''", name="pk_nonblank"),
        CheckConstraint("btrim(canonical_source_key) <> ''", name="key_nonblank"),
        CheckConstraint("octet_length(source_hash) = 32", name="hash_length"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_primary_key: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_source_key: Mapped[str] = mapped_column(Text)
    book_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_hash: Mapped[bytes] = mapped_column(LargeBinary)
    target_entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class BackfillCheckpointRecord(V2Base):
    __tablename__ = "backfill_checkpoints"
    __table_args__ = (
        CheckConstraint("btrim(snapshot_id) <> ''", name="snapshot_nonblank"),
        CheckConstraint("btrim(source_table) <> ''", name="table_nonblank"),
        CheckConstraint("octet_length(manifest_hash) = 32", name="hash_length"),
        CheckConstraint("btrim(last_canonical_source_key) <> ''", name="key_nonblank"),
        CheckConstraint("processed_count > 0", name="count_positive"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_table: Mapped[str] = mapped_column(String(96), primary_key=True)
    manifest_hash: Mapped[bytes] = mapped_column(LargeBinary)
    last_canonical_source_key: Mapped[str] = mapped_column(Text)
    processed_count: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class BackfillQuarantineRecord(V2Base):
    __tablename__ = "backfill_quarantine"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "source_table",
            "source_primary_key",
            "reason_code",
            name="uq_backfill_quarantine_source_reason",
        ),
        CheckConstraint("btrim(snapshot_id) <> ''", name="snapshot_nonblank"),
        CheckConstraint("btrim(source_table) <> ''", name="table_nonblank"),
        CheckConstraint("btrim(source_primary_key) <> ''", name="pk_nonblank"),
        CheckConstraint("btrim(reason_code) <> ''", name="reason_nonblank"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        CheckConstraint(
            "decision in ('pending','accepted','skipped')",
            name="decision_valid",
        ),
        CheckConstraint(
            "(decision = 'pending' and decided_by is null and decided_at is null) or "
            "(decision <> 'pending' and decided_by is not null and "
            "btrim(decided_by) <> '' and decided_at is not null)",
            name="decision_shape",
        ),
    )

    quarantine_id: Mapped[UUID] = mapped_column(primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(80))
    source_table: Mapped[str] = mapped_column(String(96))
    source_primary_key: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(String(96))
    details: Mapped[dict[str, object]] = mapped_column(postgresql.JSONB)
    decision: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class BackfillSealRecord(V2Base):
    __tablename__ = "backfill_seals"
    __table_args__ = (
        CheckConstraint("btrim(snapshot_id) <> ''", name="snapshot_nonblank"),
        CheckConstraint("octet_length(manifest_hash) = 32", name="hash_length"),
        CheckConstraint("jsonb_typeof(source_counts) = 'object'", name="counts_object"),
        CheckConstraint(
            "jsonb_typeof(terminal_book_hashes) = 'object'", name="hashes_object"
        ),
        CheckConstraint("quarantine_count >= 0", name="quarantine_nonnegative"),
        CheckConstraint("receipt_count >= 0", name="receipt_nonnegative"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    manifest_hash: Mapped[bytes] = mapped_column(LargeBinary)
    source_counts: Mapped[dict[str, int]] = mapped_column(postgresql.JSONB)
    terminal_book_hashes: Mapped[dict[str, str]] = mapped_column(postgresql.JSONB)
    quarantine_count: Mapped[int] = mapped_column(BigInteger)
    receipt_count: Mapped[int] = mapped_column(BigInteger)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class BackfillReviewContractRecord(V2Base):
    __tablename__ = "backfill_review_contracts"
    __table_args__ = (
        CheckConstraint("btrim(snapshot_id) <> ''", name="snapshot_nonblank"),
        CheckConstraint(
            "review_kind = 'credit_card_semantics_v1'", name="kind_supported"
        ),
        CheckConstraint("octet_length(manifest_hash) = 32", name="manifest_hash_length"),
        CheckConstraint("octet_length(review_hash) = 32", name="review_hash_length"),
        CheckConstraint("btrim(reviewer) <> ''", name="reviewer_nonblank"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    review_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest_hash: Mapped[bytes] = mapped_column(LargeBinary)
    review_hash: Mapped[bytes] = mapped_column(LargeBinary)
    reviewer: Mapped[str] = mapped_column(String(128))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


__all__ = [
    "BackfillCheckpointRecord",
    "BackfillQuarantineRecord",
    "BackfillReviewContractRecord",
    "BackfillSealRecord",
    "BackfillSourceReceiptRecord",
]
