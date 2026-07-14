from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, LargeBinary, String, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class BackfillSourceReceiptRecord(V2Base):
    __tablename__ = "backfill_source_receipts"

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

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    manifest_hash: Mapped[bytes] = mapped_column(LargeBinary)
    source_counts: Mapped[dict[str, int]] = mapped_column(postgresql.JSONB)
    terminal_book_hashes: Mapped[dict[str, str]] = mapped_column(postgresql.JSONB)
    quarantine_count: Mapped[int] = mapped_column(BigInteger)
    receipt_count: Mapped[int] = mapped_column(BigInteger)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


__all__ = [
    "BackfillCheckpointRecord",
    "BackfillQuarantineRecord",
    "BackfillSealRecord",
    "BackfillSourceReceiptRecord",
]
