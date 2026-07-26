from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


_NOW = text("clock_timestamp()")


class PreparedEntryIntentRecord(V2Base):
    __tablename__ = "prepared_entry_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "protected_content_ref"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_prepared_entry_intents_protected_content",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "committed_transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_prepared_entry_intents_committed_transaction",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "book_id",
            "actor_id",
            "intent_id",
            name="uq_prepared_entry_intents_actor_scope",
        ),
        CheckConstraint("contract_version = 1", name="contract_version_v1"),
        CheckConstraint(
            "prepared_status in "
            "('ready','needs_clarification','duplicate_suspected','unsupported')",
            name="prepared_status_valid",
        ),
        CheckConstraint(
            "lifecycle_status in ('created','consumed','cancelled')",
            name="lifecycle_status_valid",
        ),
        CheckConstraint(
            "(prepared_status = 'ready' and commit_token_hash is not null "
            "and octet_length(commit_token_hash) = 32) "
            "or (prepared_status <> 'ready' and commit_token_hash is null)",
            name="token_shape",
        ),
        CheckConstraint(
            "jsonb_typeof(canonical_payload) = 'object'",
            name="payload_object",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "(lifecycle_status = 'created' and consumed_at is null "
            "and cancelled_at is null and committed_request_id is null "
            "and committed_transaction_id is null) "
            "or (lifecycle_status = 'consumed' and consumed_at is not null "
            "and cancelled_at is null and committed_request_id is not null "
            "and committed_transaction_id is not null) "
            "or (lifecycle_status = 'cancelled' and consumed_at is null "
            "and cancelled_at is not null and committed_request_id is null "
            "and committed_transaction_id is null)",
            name="lifecycle_shape",
        ),
        Index("ix_prepared_entry_intents_expiry", "expires_at"),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    intent_id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    contract_version: Mapped[int] = mapped_column(SmallInteger)
    prepared_status: Mapped[str] = mapped_column(String(32))
    lifecycle_status: Mapped[str] = mapped_column(String(16))
    commit_token_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    canonical_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    protected_content_ref: Mapped[UUID | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=_NOW,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    committed_request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    committed_transaction_id: Mapped[UUID | None] = mapped_column(nullable=True)


class EverydayEntryExternalReferenceRecord(V2Base):
    __tablename__ = "everyday_entry_external_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_intent_id"],
            ["prepared_entry_intents.book_id", "prepared_entry_intents.intent_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name="provider_valid",
        ),
        CheckConstraint(
            "reference_kind in "
            "('provider_transaction','provider_order','import_record')",
            name="kind_valid",
        ),
        CheckConstraint(
            "octet_length(reference_hmac) = 32",
            name="reference_hmac_length",
        ),
        UniqueConstraint(
            "book_id",
            "transaction_id",
            "provider_code",
            "reference_kind",
            name="uq_everyday_entry_external_references_transaction_kind",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    provider_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    reference_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    reference_hmac: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column()
    source_intent_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=_NOW,
    )


class EverydayEntrySourceFingerprintRecord(V2Base):
    __tablename__ = "everyday_entry_source_fingerprints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_intent_id"],
            ["prepared_entry_intents.book_id", "prepared_entry_intents.intent_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "octet_length(fingerprint_hmac) = 32",
            name="fingerprint_hmac_length",
        ),
        Index(
            "ix_everyday_entry_source_fingerprints_lookup",
            "book_id",
            "fingerprint_hmac",
            "created_at",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    fingerprint_hmac: Mapped[bytes] = mapped_column(
        LargeBinary,
        primary_key=True,
    )
    source_intent_id: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=_NOW,
    )


__all__ = [
    "EverydayEntryExternalReferenceRecord",
    "EverydayEntrySourceFingerprintRecord",
    "PreparedEntryIntentRecord",
]
