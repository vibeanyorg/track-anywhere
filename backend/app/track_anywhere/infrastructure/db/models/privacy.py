from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class ProtectedDescriptionSidecarRecord(V2Base):
    __tablename__ = "protected_description_sidecars"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("btrim(kind) <> ''", name="kind_nonblank"),
        CheckConstraint(
            "algorithm = 'AES-256-GCM+HKDF-SHA256'", name="algorithm_approved"
        ),
        CheckConstraint("status in ('active', 'erased')", name="status_valid"),
        CheckConstraint(
            "status <> 'active' or (octet_length(ciphertext) >= 16 "
            "and octet_length(nonce) = 12 "
            "and key_ref ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')",
            name="active_envelope_shape",
        ),
        CheckConstraint("octet_length(content_hash) = 32", name="content_hash_length"),
        CheckConstraint(
            "(status = 'active' and erased_at is null "
            "and ciphertext is not null and key_ref is not null "
            "and nonce is not null) "
            "or (status = 'erased' and erased_at is not null "
            "and ciphertext is null and key_ref is null and nonce is null)",
            name="lifecycle_shape",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    sidecar_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    algorithm: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    erased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ImportArchiveManifestRecord(V2Base):
    __tablename__ = "import_archive_manifests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "archive_id"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_import_archive_manifests_sidecar",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("contract_version = 1", name="contract_version_v1"),
        CheckConstraint(
            "octet_length(source_dump_hash) = 32", name="source_dump_hash_length"
        ),
        CheckConstraint(
            "octet_length(source_manifest_hash) = 32",
            name="source_manifest_hash_length",
        ),
        CheckConstraint(
            "octet_length(card_review_hash) = 32", name="card_review_hash_length"
        ),
        CheckConstraint("octet_length(plan_hash) = 32", name="plan_hash_length"),
        CheckConstraint(
            "octet_length(archive_content_commitment) = 32",
            name="archive_content_commitment_length",
        ),
        CheckConstraint("octet_length(seal) = 32", name="seal_length"),
        CheckConstraint("jsonb_typeof(record_counts) = 'object'", name="counts_object"),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    archive_id: Mapped[UUID] = mapped_column(primary_key=True)
    contract_version: Mapped[int] = mapped_column(SmallInteger)
    source_dump_hash: Mapped[bytes] = mapped_column(LargeBinary)
    source_manifest_hash: Mapped[bytes] = mapped_column(LargeBinary)
    card_review_hash: Mapped[bytes] = mapped_column(LargeBinary)
    plan_hash: Mapped[bytes] = mapped_column(LargeBinary)
    archive_content_commitment: Mapped[bytes] = mapped_column(LargeBinary)
    seal: Mapped[bytes] = mapped_column(LargeBinary)
    record_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


__all__ = ["ImportArchiveManifestRecord", "ProtectedDescriptionSidecarRecord"]
