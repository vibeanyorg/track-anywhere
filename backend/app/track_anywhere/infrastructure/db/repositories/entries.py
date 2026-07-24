from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models.entries import (
    EverydayEntryExternalReferenceRecord,
    EverydayEntrySourceFingerprintRecord,
    PreparedEntryIntentRecord,
)


_PREPARED_STATUSES = frozenset(
    {"ready", "needs_clarification", "duplicate_suspected", "unsupported"}
)
_REFERENCE_KINDS = frozenset(
    {"provider_transaction", "provider_order", "import_record"}
)
_PROVIDER_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$", flags=re.ASCII)
_PRIVATE_PAYLOAD_KEYS = frozenset(
    {
        "channel",
        "external_reference",
        "line_memos",
        "merchant",
        "note",
        "purpose",
        "reference",
        "source_text",
        "transaction_memo",
    }
)


class PreparedIntentConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProposedPreparedIntent:
    """Safe compiled intent payload scoped to one authenticated actor.

    `canonical_payload` may contain resolved IDs, integer units, compiler
    decisions, and stable preview fields. Private narrative and raw source
    values must be persisted through protected content and referenced by
    `protected_content_ref`; they are rejected here to prevent accidental JSONB
    plaintext storage. Callers pass only a commit-token digest, never the token.
    """

    book_id: UUID
    actor_id: str
    intent_id: UUID
    prepared_status: str
    commit_token_hash: bytes | None = field(repr=False)
    canonical_payload: Mapping[str, object] = field(repr=False)
    expires_at: datetime
    protected_content_ref: UUID | None = None
    contract_version: int = 1

    def __post_init__(self) -> None:
        _validate_scope(self.book_id, self.actor_id, self.intent_id)
        if self.contract_version != 1:
            raise ValueError("prepared intent contract version is unsupported")
        if self.prepared_status not in _PREPARED_STATUSES:
            raise ValueError("prepared intent status is invalid")
        if (self.prepared_status == "ready") != (
            _valid_digest(self.commit_token_hash)
        ):
            raise ValueError("prepared intent token digest shape is invalid")
        if (
            not isinstance(self.canonical_payload, Mapping)
            or not self.canonical_payload
        ):
            raise ValueError("prepared intent payload must be a nonempty object")
        _validate_safe_payload(self.canonical_payload)
        if (
            type(self.expires_at) is not datetime
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("prepared intent expiry must be timezone-aware")
        if self.expires_at <= datetime.now(UTC):
            raise ValueError("prepared intent expiry must be in the future")


@dataclass(frozen=True, slots=True)
class PreparedIntentSnapshot:
    book_id: UUID
    actor_id: str
    intent_id: UUID
    contract_version: int
    prepared_status: str
    lifecycle_status: str
    commit_token_hash: bytes | None = field(repr=False)
    canonical_payload: Mapping[str, object] = field(repr=False)
    protected_content_ref: UUID | None
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None
    cancelled_at: datetime | None
    committed_request_id: UUID | None
    committed_transaction_id: UUID | None


@dataclass(frozen=True, slots=True)
class ProposedExternalReference:
    """A strong duplicate key containing only a keyed HMAC of the raw value."""

    book_id: UUID
    transaction_id: UUID
    source_intent_id: UUID
    provider_code: str
    reference_kind: str
    reference_hmac: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not all(
            type(value) is UUID
            for value in (self.book_id, self.transaction_id, self.source_intent_id)
        ):
            raise TypeError("external reference coordinates must be UUIDs")
        if (
            type(self.provider_code) is not str
            or _PROVIDER_CODE.fullmatch(self.provider_code) is None
        ):
            raise ValueError("external reference provider is invalid")
        if self.reference_kind not in _REFERENCE_KINDS:
            raise ValueError("external reference kind is invalid")
        if not _valid_digest(self.reference_hmac):
            raise ValueError("external reference digest is invalid")


@dataclass(frozen=True, slots=True)
class ExternalDuplicateEvidence:
    book_id: UUID
    transaction_id: UUID
    provider_code: str
    reference_kind: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProposedSourceFingerprint:
    """Soft duplicate evidence containing only a keyed HMAC."""

    book_id: UUID
    transaction_id: UUID
    source_intent_id: UUID
    fingerprint_hmac: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not all(
            type(value) is UUID
            for value in (self.book_id, self.transaction_id, self.source_intent_id)
        ):
            raise TypeError("source fingerprint coordinates must be UUIDs")
        if not _valid_digest(self.fingerprint_hmac):
            raise ValueError("source fingerprint digest is invalid")


@dataclass(frozen=True, slots=True)
class SourceFingerprintEvidence:
    book_id: UUID
    transaction_id: UUID
    created_at: datetime


class PreparedEntryIntentRepository:
    """Book + actor + intent scoped persistence for preview/commit continuity.

    The frozen commit payload intentionally omits actor identity. The
    request-scoped service must inject its authenticated actor into every
    repository call; no method offers a global intent lookup.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        *,
        book_id: UUID,
        actor_id: str,
        intent_id: UUID,
    ) -> PreparedIntentSnapshot | None:
        _validate_scope(book_id, actor_id, intent_id)
        record = self._session.execute(
            self._scope(book_id=book_id, actor_id=actor_id, intent_id=intent_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        return None if record is None else _intent_snapshot(record)

    def insert_or_exact_get(
        self,
        proposed: ProposedPreparedIntent,
    ) -> PreparedIntentSnapshot:
        if type(proposed) is not ProposedPreparedIntent:
            raise TypeError("prepared intent proposal is invalid")
        self._session.execute(
            pg_insert(PreparedEntryIntentRecord)
            .values(
                book_id=proposed.book_id,
                actor_id=proposed.actor_id,
                intent_id=proposed.intent_id,
                contract_version=proposed.contract_version,
                prepared_status=proposed.prepared_status,
                lifecycle_status="created",
                commit_token_hash=proposed.commit_token_hash,
                canonical_payload=dict(proposed.canonical_payload),
                protected_content_ref=proposed.protected_content_ref,
                expires_at=proposed.expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=("book_id", "intent_id"),
            )
        )
        snapshot = self.get(
            book_id=proposed.book_id,
            actor_id=proposed.actor_id,
            intent_id=proposed.intent_id,
        )
        if snapshot is None or not _intent_matches(snapshot, proposed):
            raise PreparedIntentConflict(
                "prepared intent conflicts with existing data"
            )
        return snapshot

    def claim_ready(
        self,
        *,
        book_id: UUID,
        actor_id: str,
        intent_id: UUID,
        commit_token_hash: bytes,
        request_id: UUID,
        transaction_id: UUID,
    ) -> PreparedIntentSnapshot | None:
        _validate_scope(book_id, actor_id, intent_id)
        if not _valid_digest(commit_token_hash):
            raise ValueError("commit token digest is invalid")
        if type(request_id) is not UUID or type(transaction_id) is not UUID:
            raise TypeError("commit coordinates must be UUIDs")
        record = self._session.execute(
            update(PreparedEntryIntentRecord)
            .where(
                PreparedEntryIntentRecord.book_id == book_id,
                PreparedEntryIntentRecord.actor_id == actor_id,
                PreparedEntryIntentRecord.intent_id == intent_id,
                PreparedEntryIntentRecord.prepared_status == "ready",
                PreparedEntryIntentRecord.lifecycle_status == "created",
                PreparedEntryIntentRecord.commit_token_hash == commit_token_hash,
                PreparedEntryIntentRecord.expires_at
                > func.clock_timestamp(),
            )
            .values(
                lifecycle_status="consumed",
                consumed_at=func.clock_timestamp(),
                committed_request_id=request_id,
                committed_transaction_id=transaction_id,
            )
            .returning(PreparedEntryIntentRecord)
        ).scalar_one_or_none()
        return None if record is None else _intent_snapshot(record)

    def cancel(
        self,
        *,
        book_id: UUID,
        actor_id: str,
        intent_id: UUID,
    ) -> PreparedIntentSnapshot | None:
        _validate_scope(book_id, actor_id, intent_id)
        record = self._session.execute(
            update(PreparedEntryIntentRecord)
            .where(
                PreparedEntryIntentRecord.book_id == book_id,
                PreparedEntryIntentRecord.actor_id == actor_id,
                PreparedEntryIntentRecord.intent_id == intent_id,
                PreparedEntryIntentRecord.lifecycle_status == "created",
            )
            .values(
                lifecycle_status="cancelled",
                cancelled_at=func.clock_timestamp(),
            )
            .returning(PreparedEntryIntentRecord)
        ).scalar_one_or_none()
        return None if record is None else _intent_snapshot(record)

    @staticmethod
    def _scope(
        *,
        book_id: UUID,
        actor_id: str,
        intent_id: UUID,
    ) -> Select[tuple[PreparedEntryIntentRecord]]:
        return select(PreparedEntryIntentRecord).where(
            PreparedEntryIntentRecord.book_id == book_id,
            PreparedEntryIntentRecord.actor_id == actor_id,
            PreparedEntryIntentRecord.intent_id == intent_id,
        )


class EverydayEntryDuplicateRepository:
    """Book-scoped committed duplicate evidence for Everyday Entry only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_external_reference(
        self,
        *,
        book_id: UUID,
        provider_code: str,
        reference_kind: str,
        reference_hmac: bytes,
    ) -> ExternalDuplicateEvidence | None:
        if type(book_id) is not UUID or not _valid_digest(reference_hmac):
            raise ValueError("external reference lookup is invalid")
        record = self._session.execute(
            select(EverydayEntryExternalReferenceRecord).where(
                EverydayEntryExternalReferenceRecord.book_id == book_id,
                EverydayEntryExternalReferenceRecord.provider_code == provider_code,
                EverydayEntryExternalReferenceRecord.reference_kind == reference_kind,
                EverydayEntryExternalReferenceRecord.reference_hmac == reference_hmac,
            )
        ).scalar_one_or_none()
        return None if record is None else _external_reference_snapshot(record)

    def insert_external_reference_or_get(
        self,
        proposed: ProposedExternalReference,
    ) -> tuple[ExternalDuplicateEvidence, bool]:
        if type(proposed) is not ProposedExternalReference:
            raise TypeError("external reference proposal is invalid")
        inserted = self._session.execute(
            pg_insert(EverydayEntryExternalReferenceRecord)
            .values(
                book_id=proposed.book_id,
                provider_code=proposed.provider_code,
                reference_kind=proposed.reference_kind,
                reference_hmac=proposed.reference_hmac,
                transaction_id=proposed.transaction_id,
                source_intent_id=proposed.source_intent_id,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    "book_id",
                    "provider_code",
                    "reference_kind",
                    "reference_hmac",
                )
            )
            .returning(EverydayEntryExternalReferenceRecord)
        ).scalar_one_or_none()
        if inserted is not None:
            return _external_reference_snapshot(inserted), True
        existing = self.get_external_reference(
            book_id=proposed.book_id,
            provider_code=proposed.provider_code,
            reference_kind=proposed.reference_kind,
            reference_hmac=proposed.reference_hmac,
        )
        if existing is None:
            raise RuntimeError("external reference persistence failed")
        return existing, False

    def find_source_fingerprints(
        self,
        *,
        book_id: UUID,
        fingerprint_hmac: bytes,
        created_since: datetime,
    ) -> tuple[SourceFingerprintEvidence, ...]:
        if type(book_id) is not UUID or not _valid_digest(fingerprint_hmac):
            raise ValueError("source fingerprint lookup is invalid")
        if (
            type(created_since) is not datetime
            or created_since.tzinfo is None
            or created_since.utcoffset() is None
        ):
            raise ValueError("source fingerprint cutoff must be timezone-aware")
        records = self._session.execute(
            select(EverydayEntrySourceFingerprintRecord)
            .where(
                EverydayEntrySourceFingerprintRecord.book_id == book_id,
                EverydayEntrySourceFingerprintRecord.fingerprint_hmac
                == fingerprint_hmac,
                EverydayEntrySourceFingerprintRecord.created_at >= created_since,
            )
            .order_by(
                EverydayEntrySourceFingerprintRecord.created_at,
                EverydayEntrySourceFingerprintRecord.transaction_id,
            )
        ).scalars()
        return tuple(_source_fingerprint_snapshot(record) for record in records)

    def insert_source_fingerprint(
        self,
        proposed: ProposedSourceFingerprint,
    ) -> SourceFingerprintEvidence:
        if type(proposed) is not ProposedSourceFingerprint:
            raise TypeError("source fingerprint proposal is invalid")
        record = self._session.execute(
            pg_insert(EverydayEntrySourceFingerprintRecord)
            .values(
                book_id=proposed.book_id,
                transaction_id=proposed.transaction_id,
                source_intent_id=proposed.source_intent_id,
                fingerprint_hmac=proposed.fingerprint_hmac,
            )
            .on_conflict_do_nothing(
                index_elements=("book_id", "transaction_id", "fingerprint_hmac")
            )
            .returning(EverydayEntrySourceFingerprintRecord)
        ).scalar_one_or_none()
        if record is None:
            record = self._session.execute(
                select(EverydayEntrySourceFingerprintRecord).where(
                    EverydayEntrySourceFingerprintRecord.book_id
                    == proposed.book_id,
                    EverydayEntrySourceFingerprintRecord.transaction_id
                    == proposed.transaction_id,
                    EverydayEntrySourceFingerprintRecord.fingerprint_hmac
                    == proposed.fingerprint_hmac,
                )
            ).scalar_one_or_none()
        if record is None or record.source_intent_id != proposed.source_intent_id:
            raise RuntimeError("source fingerprint persistence failed")
        return _source_fingerprint_snapshot(record)


def hash_commit_token(token: str) -> bytes:
    """Return the only representation of an opaque commit token that may persist."""

    if type(token) is not str or not 32 <= len(token) <= 512:
        raise ValueError("commit token shape is invalid")
    return hashlib.sha256(token.encode("utf-8")).digest()


def hmac_external_reference(
    *,
    key: bytes,
    provider_code: str,
    reference_kind: str,
    reference: str,
) -> bytes:
    return _keyed_digest(
        key=key,
        purpose=b"external-reference",
        parts=(provider_code, reference_kind, reference),
    )


def hmac_source_fingerprint(
    *,
    key: bytes,
    normalized_parts: tuple[str, ...],
) -> bytes:
    if not normalized_parts:
        raise ValueError("source fingerprint inputs are invalid")
    return _keyed_digest(
        key=key,
        purpose=b"source-fingerprint",
        parts=normalized_parts,
    )


def _keyed_digest(
    *,
    key: bytes,
    purpose: bytes,
    parts: tuple[str, ...],
) -> bytes:
    if type(key) is not bytes or len(key) < 32:
        raise ValueError("duplicate-detection key is invalid")
    if any(type(part) is not str or not part for part in parts):
        raise ValueError("duplicate-detection inputs are invalid")
    digest = hmac.new(key, digestmod=hashlib.sha256)
    digest.update(b"track-anywhere:eeg:")
    digest.update(purpose)
    digest.update(b":v1")
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _validate_scope(book_id: UUID, actor_id: str, intent_id: UUID) -> None:
    if type(book_id) is not UUID or type(intent_id) is not UUID:
        raise TypeError("prepared intent coordinates must be UUIDs")
    if (
        type(actor_id) is not str
        or not actor_id.strip()
        or len(actor_id.encode("utf-8")) > 128
    ):
        raise ValueError("prepared intent actor is invalid")


def _valid_digest(value: object) -> bool:
    return type(value) is bytes and len(value) == 32


def _validate_safe_payload(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("prepared intent payload keys must be strings")
            if key.casefold() in _PRIVATE_PAYLOAD_KEYS:
                raise ValueError(
                    "private entry fields require protected-content storage"
                )
            _validate_safe_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_safe_payload(child)
    elif value is not None and type(value) not in {bool, int, str}:
        raise ValueError(
            "prepared intent payload must use exact canonical JSON values"
        )


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _intent_snapshot(record: PreparedEntryIntentRecord) -> PreparedIntentSnapshot:
    payload = _freeze_json(record.canonical_payload)
    if not isinstance(payload, Mapping):
        raise RuntimeError("prepared intent payload invariant failed")
    return PreparedIntentSnapshot(
        book_id=record.book_id,
        actor_id=record.actor_id,
        intent_id=record.intent_id,
        contract_version=record.contract_version,
        prepared_status=record.prepared_status,
        lifecycle_status=record.lifecycle_status,
        commit_token_hash=record.commit_token_hash,
        canonical_payload=payload,
        protected_content_ref=record.protected_content_ref,
        expires_at=record.expires_at,
        created_at=record.created_at,
        consumed_at=record.consumed_at,
        cancelled_at=record.cancelled_at,
        committed_request_id=record.committed_request_id,
        committed_transaction_id=record.committed_transaction_id,
    )


def _intent_matches(
    snapshot: PreparedIntentSnapshot,
    proposed: ProposedPreparedIntent,
) -> bool:
    return (
        snapshot.book_id == proposed.book_id
        and snapshot.actor_id == proposed.actor_id
        and snapshot.intent_id == proposed.intent_id
        and snapshot.contract_version == proposed.contract_version
        and snapshot.prepared_status == proposed.prepared_status
        and snapshot.lifecycle_status == "created"
        and snapshot.commit_token_hash == proposed.commit_token_hash
        and snapshot.canonical_payload == _freeze_json(proposed.canonical_payload)
        and snapshot.protected_content_ref == proposed.protected_content_ref
        and snapshot.expires_at == proposed.expires_at
    )


def _external_reference_snapshot(
    record: EverydayEntryExternalReferenceRecord,
) -> ExternalDuplicateEvidence:
    return ExternalDuplicateEvidence(
        book_id=record.book_id,
        transaction_id=record.transaction_id,
        provider_code=record.provider_code,
        reference_kind=record.reference_kind,
        created_at=record.created_at,
    )


def _source_fingerprint_snapshot(
    record: EverydayEntrySourceFingerprintRecord,
) -> SourceFingerprintEvidence:
    return SourceFingerprintEvidence(
        book_id=record.book_id,
        transaction_id=record.transaction_id,
        created_at=record.created_at,
    )


__all__ = [
    "EverydayEntryDuplicateRepository",
    "ExternalDuplicateEvidence",
    "PreparedEntryIntentRepository",
    "PreparedIntentConflict",
    "PreparedIntentSnapshot",
    "ProposedExternalReference",
    "ProposedPreparedIntent",
    "ProposedSourceFingerprint",
    "SourceFingerprintEvidence",
    "hash_commit_token",
    "hmac_external_reference",
    "hmac_source_fingerprint",
]
