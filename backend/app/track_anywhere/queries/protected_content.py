from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from ..application.privacy.protected_content import TransactionDescription
from ..application.privacy.service import (
    ProtectedContentConflict,
    ProtectedContentService,
)
from ..infrastructure.crypto import ProtectedContentCipher
from ..infrastructure.db.repositories.privacy import ProtectedContentRepository
from ..serialization.canonical_json import canonical_json_bytes


class ProtectedContentUnavailable(RuntimeError):
    pass


class ProtectedContentErased(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImportArchiveMetadata:
    archive_id: UUID
    contract_version: int
    source_dump_hash: bytes = field(repr=False)
    source_manifest_hash: bytes = field(repr=False)
    card_review_hash: bytes = field(repr=False)
    plan_hash: bytes = field(repr=False)
    content_commitment: bytes = field(repr=False)
    seal: bytes = field(repr=False)
    record_counts: Mapping[str, int]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportArchiveExport:
    archive_id: UUID
    content_commitment: bytes = field(repr=False)
    seal: bytes = field(repr=False)
    canonical_ndjson: bytes = field(repr=False)


def get_transaction_descriptions(
    session: Session,
    book_id: UUID,
    *,
    description_refs: tuple[UUID, ...],
    cipher: ProtectedContentCipher,
    repository: ProtectedContentRepository | None = None,
) -> dict[UUID, TransactionDescription]:
    if type(book_id) is not UUID or any(type(value) is not UUID for value in description_refs):
        raise ProtectedContentUnavailable("protected content is unavailable")
    unique_refs = tuple(dict.fromkeys(description_refs))
    if not unique_refs:
        return {}
    content_repository = repository or ProtectedContentRepository()
    snapshots = content_repository.get_active_batch(
        session,
        book_id=book_id,
        sidecar_ids=unique_refs,
    )
    if set(snapshots) != set(unique_refs):
        missing_refs = tuple(
            sidecar_id for sidecar_id in unique_refs if sidecar_id not in snapshots
        )
        unavailable = content_repository.get_batch(
            session,
            book_id=book_id,
            sidecar_ids=missing_refs,
        )
        if any(snapshot.status == "erased" for snapshot in unavailable.values()):
            raise ProtectedContentErased("protected content was erased")
        raise ProtectedContentUnavailable("protected content is unavailable")
    service = ProtectedContentService(
        cipher=cipher,
        repository=content_repository,
    )
    result: dict[UUID, TransactionDescription] = {}
    for sidecar_id in unique_refs:
        try:
            plaintext = service.decrypt_active(
                snapshots[sidecar_id],
                expected_kind="transaction_description",
            )
            result[sidecar_id] = _decode_transaction_description(plaintext)
        except ProtectedContentConflict:
            raise ProtectedContentUnavailable(
                "protected content is unavailable"
            ) from None
    return result


def list_import_archives(
    session: Session,
    book_id: UUID,
    *,
    cipher: ProtectedContentCipher,
    repository: ProtectedContentRepository | None = None,
) -> tuple[ImportArchiveMetadata, ...]:
    content_repository = repository or ProtectedContentRepository()
    service = ProtectedContentService(
        cipher=cipher,
        repository=content_repository,
    )
    manifests = content_repository.list_archive_manifests(session, book_id=book_id)
    return tuple(
        _verified_archive_metadata(service, manifest)
        for manifest in manifests
    )


def export_import_archive(
    session: Session,
    book_id: UUID,
    archive_id: UUID,
    *,
    cipher: ProtectedContentCipher,
    repository: ProtectedContentRepository | None = None,
) -> ImportArchiveExport:
    content_repository = repository or ProtectedContentRepository()
    manifest = content_repository.get_archive_manifest(
        session,
        book_id=book_id,
        archive_id=archive_id,
    )
    if manifest is None:
        raise LookupError("import archive not found")
    if manifest.sidecar.status == "erased":
        raise ProtectedContentErased("protected content was erased")
    service = ProtectedContentService(
        cipher=cipher,
        repository=content_repository,
    )
    try:
        plaintext = service.verify_archive_manifest(
            manifest,
            include_content=True,
        )
    except ProtectedContentConflict:
        raise ProtectedContentUnavailable("protected content is unavailable") from None
    if plaintext is None:
        raise ProtectedContentUnavailable("protected content is unavailable")
    return ImportArchiveExport(
        archive_id=manifest.archive_id,
        content_commitment=manifest.archive_content_commitment,
        seal=manifest.seal,
        canonical_ndjson=plaintext,
    )


def _verified_archive_metadata(
    service: ProtectedContentService,
    manifest,
) -> ImportArchiveMetadata:
    if manifest.sidecar.status == "erased":
        raise ProtectedContentErased("protected content was erased")
    try:
        service.verify_archive_manifest(manifest, include_content=False)
    except ProtectedContentConflict:
        raise ProtectedContentUnavailable("protected content is unavailable") from None
    return ImportArchiveMetadata(
        archive_id=manifest.archive_id,
        contract_version=manifest.contract_version,
        source_dump_hash=manifest.source_dump_hash,
        source_manifest_hash=manifest.source_manifest_hash,
        card_review_hash=manifest.card_review_hash,
        plan_hash=manifest.plan_hash,
        content_commitment=manifest.archive_content_commitment,
        seal=manifest.seal,
        record_counts=manifest.record_counts,
        created_at=manifest.created_at,
    )


def _decode_transaction_description(plaintext: bytes) -> TransactionDescription:
    try:
        if type(plaintext) is not bytes:
            raise TypeError
        parsed = json.loads(
            plaintext.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if type(parsed) is not dict or set(parsed) != {
            "purpose",
            "transaction_memo",
            "line_memos",
        }:
            raise ValueError
        purpose = parsed["purpose"]
        transaction_memo = parsed["transaction_memo"]
        line_memos = parsed["line_memos"]
        if (
            (purpose is not None and type(purpose) is not str)
            or (transaction_memo is not None and type(transaction_memo) is not str)
            or type(line_memos) is not list
            or any(value is not None and type(value) is not str for value in line_memos)
        ):
            raise ValueError
        description = TransactionDescription(
            purpose=purpose,
            transaction_memo=transaction_memo,
            line_memos=tuple(line_memos),
        )
        canonical = canonical_json_bytes(description.model_dump(mode="json"))
        if not hmac.compare_digest(canonical, plaintext):
            raise ValueError
        return description
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ProtectedContentUnavailable("protected content is unavailable") from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


__all__ = [
    "ImportArchiveExport",
    "ImportArchiveMetadata",
    "ProtectedContentErased",
    "ProtectedContentUnavailable",
    "export_import_archive",
    "get_transaction_descriptions",
    "list_import_archives",
]
