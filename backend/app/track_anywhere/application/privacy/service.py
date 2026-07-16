from __future__ import annotations

import hmac
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBytes, StrictInt
from sqlalchemy.orm import Session

from ...domain.privacy import FrozenContract
from ...infrastructure.crypto import (
    PROTECTED_CONTENT_ALGORITHM,
    ProtectedContentCipher,
    ProtectedContentDecryptionError,
    ProtectedContentError,
    SealedProtectedContent,
)
from ...infrastructure.db.repositories.privacy import (
    ImportArchiveManifestSnapshot,
    ProposedImportArchiveManifest,
    ProposedProtectedContent,
    ProtectedContentNotFound,
    ProtectedContentRepository,
    ProtectedContentSnapshot,
)
from ...serialization.canonical_json import canonical_json_bytes
from .protected_content import ProtectedContentKind


NonnegativeCount = Annotated[StrictInt, Field(ge=0)]


class ImportArchiveRecordCounts(FrozenContract):
    classification_audit_records: NonnegativeCount
    investment_activities: NonnegativeCount
    investment_valuations: NonnegativeCount
    uncategorized_fx_reporting_facts: NonnegativeCount
    institution_metadata_records: NonnegativeCount
    counterparty_records: NonnegativeCount
    omission_records: NonnegativeCount


class ImportArchiveProposal(FrozenContract):
    contract_version: Literal[1]
    book_id: UUID
    archive_id: UUID
    source_dump_hash: StrictBytes = Field(min_length=32, max_length=32, repr=False)
    source_manifest_hash: StrictBytes = Field(
        min_length=32, max_length=32, repr=False
    )
    card_review_hash: StrictBytes = Field(min_length=32, max_length=32, repr=False)
    plan_hash: StrictBytes = Field(min_length=32, max_length=32, repr=False)
    record_counts: ImportArchiveRecordCounts
    canonical_ndjson: StrictBytes = Field(min_length=1, repr=False)


class ProtectedContentConflict(ValueError):
    pass


class ProtectedContentService:
    def __init__(
        self,
        *,
        cipher: ProtectedContentCipher,
        repository: ProtectedContentRepository,
    ) -> None:
        self._cipher = cipher
        self._repository = repository

    def create_or_exact_verify(
        self,
        session: Session,
        *,
        book_id: UUID,
        sidecar_id: UUID,
        kind: ProtectedContentKind,
        canonical_plaintext: bytes,
    ) -> ProtectedContentSnapshot:
        existing = self._repository.get(
            session,
            book_id=book_id,
            sidecar_id=sidecar_id,
        )
        if existing is None:
            sealed = self._cipher.encrypt(
                book_id=book_id,
                sidecar_id=sidecar_id,
                kind=kind,
                plaintext=canonical_plaintext,
            )
            existing = self._repository.insert_or_get(
                session,
                ProposedProtectedContent(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    kind=kind,
                    ciphertext=sealed.ciphertext,
                    key_ref=sealed.key_ref,
                    nonce=sealed.nonce,
                    algorithm=sealed.algorithm,
                    content_hash=sealed.content_hash,
                ),
            )
        return self._verify_exact(
            existing,
            kind=kind,
            canonical_plaintext=canonical_plaintext,
        )

    def erase(
        self,
        session: Session,
        *,
        book_id: UUID,
        sidecar_id: UUID,
    ) -> ProtectedContentSnapshot:
        try:
            return self._repository.erase(
                session,
                book_id=book_id,
                sidecar_id=sidecar_id,
            )
        except ProtectedContentNotFound:
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            ) from None

    def decrypt_active(
        self,
        content: ProtectedContentSnapshot,
        *,
        expected_kind: ProtectedContentKind,
    ) -> bytes:
        if (
            type(content) is not ProtectedContentSnapshot
            or content.status != "active"
            or content.kind != expected_kind
            or content.ciphertext is None
            or content.key_ref is None
            or content.nonce is None
        ):
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            )
        try:
            return self._cipher.decrypt(
                book_id=content.book_id,
                sidecar_id=content.sidecar_id,
                kind=expected_kind,
                sealed=SealedProtectedContent(
                    key_ref=content.key_ref,
                    algorithm=content.algorithm,
                    content_hash=content.content_hash,
                    ciphertext=content.ciphertext,
                    nonce=content.nonce,
                ),
            )
        except ProtectedContentDecryptionError:
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            ) from None

    def create_or_exact_verify_archive(
        self,
        session: Session,
        *,
        archive: ImportArchiveProposal,
    ) -> ImportArchiveManifestSnapshot:
        if type(archive) is not ImportArchiveProposal:
            raise ProtectedContentConflict(
                "import archive conflicts with existing data"
            )
        canonical_ndjson = _canonicalize_ndjson(archive.canonical_ndjson)
        sidecar = self.create_or_exact_verify(
            session,
            book_id=archive.book_id,
            sidecar_id=archive.archive_id,
            kind="import_archive",
            canonical_plaintext=canonical_ndjson,
        )
        if sidecar.key_ref is None:
            raise ProtectedContentConflict(
                "import archive conflicts with existing data"
            )
        record_counts = archive.record_counts.model_dump(mode="python")
        seal = self._cipher.commit_archive_seal(
            book_id=archive.book_id,
            archive_id=archive.archive_id,
            key_ref=sidecar.key_ref,
            contract_version=archive.contract_version,
            source_dump_hash=archive.source_dump_hash,
            source_manifest_hash=archive.source_manifest_hash,
            card_review_hash=archive.card_review_hash,
            plan_hash=archive.plan_hash,
            archive_content_commitment=sidecar.content_hash,
            record_counts=record_counts,
        )
        existing = self._repository.insert_archive_manifest_or_get(
            session,
            ProposedImportArchiveManifest(
                book_id=archive.book_id,
                archive_id=archive.archive_id,
                contract_version=archive.contract_version,
                source_dump_hash=archive.source_dump_hash,
                source_manifest_hash=archive.source_manifest_hash,
                card_review_hash=archive.card_review_hash,
                plan_hash=archive.plan_hash,
                archive_content_commitment=sidecar.content_hash,
                seal=seal,
                record_counts=record_counts,
            ),
        )
        if not self._archive_matches(
            existing,
            archive=archive,
            sidecar=sidecar,
            seal=seal,
            record_counts=record_counts,
        ):
            raise ProtectedContentConflict(
                "import archive conflicts with existing data"
            )
        return existing

    def verify_archive_manifest(
        self,
        manifest: ImportArchiveManifestSnapshot,
        *,
        include_content: bool,
    ) -> bytes | None:
        if type(manifest) is not ImportArchiveManifestSnapshot:
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            )
        sidecar = manifest.sidecar
        if (
            sidecar.book_id != manifest.book_id
            or sidecar.sidecar_id != manifest.archive_id
            or sidecar.status != "active"
            or sidecar.kind != "import_archive"
            or type(sidecar.ciphertext) is not bytes
            or len(sidecar.ciphertext) < 16
            or sidecar.key_ref is None
            or type(sidecar.nonce) is not bytes
            or len(sidecar.nonce) != 12
            or sidecar.algorithm != PROTECTED_CONTENT_ALGORITHM
            or not _is_digest(sidecar.content_hash)
            or any(
                not _is_digest(value)
                for value in (
                    manifest.source_dump_hash,
                    manifest.source_manifest_hash,
                    manifest.card_review_hash,
                    manifest.plan_hash,
                    manifest.archive_content_commitment,
                    manifest.seal,
                )
            )
            or not hmac.compare_digest(
                manifest.archive_content_commitment,
                sidecar.content_hash,
            )
        ):
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            )
        try:
            expected_seal = self._cipher.commit_archive_seal(
                book_id=manifest.book_id,
                archive_id=manifest.archive_id,
                key_ref=sidecar.key_ref,
                contract_version=manifest.contract_version,
                source_dump_hash=manifest.source_dump_hash,
                source_manifest_hash=manifest.source_manifest_hash,
                card_review_hash=manifest.card_review_hash,
                plan_hash=manifest.plan_hash,
                archive_content_commitment=manifest.archive_content_commitment,
                record_counts=dict(manifest.record_counts),
            )
        except ProtectedContentError:
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            ) from None
        if not hmac.compare_digest(expected_seal, manifest.seal):
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            )
        if not include_content:
            return None
        plaintext = self.decrypt_active(
            sidecar,
            expected_kind="import_archive",
        )
        canonical = _canonicalize_ndjson(plaintext)
        if not hmac.compare_digest(canonical, plaintext):
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            )
        return plaintext

    def _verify_exact(
        self,
        existing: ProtectedContentSnapshot,
        *,
        kind: ProtectedContentKind,
        canonical_plaintext: bytes,
    ) -> ProtectedContentSnapshot:
        decrypted = self.decrypt_active(existing, expected_kind=kind)
        if type(canonical_plaintext) is not bytes or not hmac.compare_digest(
            decrypted, canonical_plaintext
        ):
            raise ProtectedContentConflict(
                "protected content conflicts with existing data"
            )
        return existing

    @staticmethod
    def _archive_matches(
        existing: ImportArchiveManifestSnapshot,
        *,
        archive: ImportArchiveProposal,
        sidecar: ProtectedContentSnapshot,
        seal: bytes,
        record_counts: dict[str, int],
    ) -> bool:
        return (
            existing.book_id == archive.book_id
            and existing.archive_id == archive.archive_id
            and existing.contract_version == archive.contract_version
            and existing.record_counts == record_counts
            and existing.sidecar.status == "active"
            and existing.sidecar.kind == "import_archive"
            and hmac.compare_digest(
                existing.source_dump_hash, archive.source_dump_hash
            )
            and hmac.compare_digest(
                existing.source_manifest_hash, archive.source_manifest_hash
            )
            and hmac.compare_digest(
                existing.card_review_hash, archive.card_review_hash
            )
            and hmac.compare_digest(existing.plan_hash, archive.plan_hash)
            and hmac.compare_digest(
                existing.archive_content_commitment, sidecar.content_hash
            )
            and hmac.compare_digest(existing.seal, seal)
        )


def _canonicalize_ndjson(value: bytes) -> bytes:
    try:
        if type(value) is not bytes:
            raise TypeError
        decoded = value.decode("utf-8")
        lines = decoded.splitlines()
        if not lines or any(not line.strip() for line in lines):
            raise ValueError
        canonical_lines = []
        for line in lines:
            parsed = json.loads(
                line,
                object_pairs_hook=_unique_json_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if type(parsed) is not dict:
                raise ValueError
            canonical_lines.append(canonical_json_bytes(parsed))
        return b"\n".join(canonical_lines) + b"\n"
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ProtectedContentConflict("import archive content is invalid") from None


def _is_digest(value: object) -> bool:
    return type(value) is bytes and len(value) == 32


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


__all__ = [
    "ImportArchiveProposal",
    "ImportArchiveRecordCounts",
    "ProtectedContentConflict",
    "ProtectedContentService",
]
