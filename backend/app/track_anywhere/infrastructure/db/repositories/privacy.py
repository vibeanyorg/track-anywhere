from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models.privacy import (
    ImportArchiveManifestRecord,
    ProtectedDescriptionSidecarRecord,
)


@dataclass(frozen=True, slots=True)
class ProposedProtectedContent:
    book_id: UUID
    sidecar_id: UUID
    kind: str
    ciphertext: bytes = field(repr=False)
    key_ref: str
    nonce: bytes = field(repr=False)
    algorithm: str
    content_hash: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProtectedContentSnapshot:
    book_id: UUID
    sidecar_id: UUID
    kind: str
    ciphertext: bytes | None = field(repr=False)
    key_ref: str | None
    nonce: bytes | None = field(repr=False)
    algorithm: str
    content_hash: bytes = field(repr=False)
    status: str
    created_at: datetime
    erased_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProposedImportArchiveManifest:
    book_id: UUID
    archive_id: UUID
    contract_version: int
    source_dump_hash: bytes = field(repr=False)
    source_manifest_hash: bytes = field(repr=False)
    card_review_hash: bytes = field(repr=False)
    plan_hash: bytes = field(repr=False)
    archive_content_commitment: bytes = field(repr=False)
    seal: bytes = field(repr=False)
    record_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ImportArchiveManifestSnapshot:
    book_id: UUID
    archive_id: UUID
    contract_version: int
    source_dump_hash: bytes = field(repr=False)
    source_manifest_hash: bytes = field(repr=False)
    card_review_hash: bytes = field(repr=False)
    plan_hash: bytes = field(repr=False)
    archive_content_commitment: bytes = field(repr=False)
    seal: bytes = field(repr=False)
    record_counts: Mapping[str, int]
    created_at: datetime
    sidecar: ProtectedContentSnapshot = field(repr=False)


class ProtectedContentNotFound(LookupError):
    pass


class ProtectedContentRepository:
    def get(
        self,
        session: Session,
        *,
        book_id: UUID,
        sidecar_id: UUID,
    ) -> ProtectedContentSnapshot | None:
        record = session.execute(
            select(ProtectedDescriptionSidecarRecord)
            .where(
                ProtectedDescriptionSidecarRecord.book_id == book_id,
                ProtectedDescriptionSidecarRecord.sidecar_id == sidecar_id,
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        return None if record is None else self._snapshot(record)

    def insert_or_get(
        self,
        session: Session,
        proposed: ProposedProtectedContent,
    ) -> ProtectedContentSnapshot:
        session.execute(
            pg_insert(ProtectedDescriptionSidecarRecord)
            .values(
                book_id=proposed.book_id,
                sidecar_id=proposed.sidecar_id,
                kind=proposed.kind,
                ciphertext=proposed.ciphertext,
                key_ref=proposed.key_ref,
                nonce=proposed.nonce,
                algorithm=proposed.algorithm,
                content_hash=proposed.content_hash,
                status="active",
                erased_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=("book_id", "sidecar_id"),
            )
        )
        result = self.get(
            session,
            book_id=proposed.book_id,
            sidecar_id=proposed.sidecar_id,
        )
        if result is None:
            raise RuntimeError("protected content persistence failed")
        return result

    def get_active_batch(
        self,
        session: Session,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> dict[UUID, ProtectedContentSnapshot]:
        if not sidecar_ids:
            return {}
        records = session.execute(
            select(ProtectedDescriptionSidecarRecord)
            .where(
                ProtectedDescriptionSidecarRecord.book_id == book_id,
                ProtectedDescriptionSidecarRecord.sidecar_id.in_(sidecar_ids),
                ProtectedDescriptionSidecarRecord.status == "active",
            )
            .execution_options(populate_existing=True)
        ).scalars()
        by_id = {record.sidecar_id: self._snapshot(record) for record in records}
        return {
            sidecar_id: by_id[sidecar_id]
            for sidecar_id in sidecar_ids
            if sidecar_id in by_id
        }

    def get_batch(
        self,
        session: Session,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> dict[UUID, ProtectedContentSnapshot]:
        if not sidecar_ids:
            return {}
        records = session.execute(
            select(ProtectedDescriptionSidecarRecord)
            .where(
                ProtectedDescriptionSidecarRecord.book_id == book_id,
                ProtectedDescriptionSidecarRecord.sidecar_id.in_(sidecar_ids),
            )
            .execution_options(populate_existing=True)
        ).scalars()
        by_id = {record.sidecar_id: self._snapshot(record) for record in records}
        return {
            sidecar_id: by_id[sidecar_id]
            for sidecar_id in sidecar_ids
            if sidecar_id in by_id
        }

    def erase(
        self,
        session: Session,
        *,
        book_id: UUID,
        sidecar_id: UUID,
    ) -> ProtectedContentSnapshot:
        erasure_result = session.execute(
            text(
                "select public.v2_erase_protected_content(:book_id, :sidecar_id)"
            ),
            {"book_id": book_id, "sidecar_id": sidecar_id},
        ).scalar_one()
        if erasure_result is None:
            raise ProtectedContentNotFound(
                "protected content was not found in the requested scope"
            )
        if type(erasure_result) is not bool:
            raise RuntimeError("protected content erasure result is invalid")
        snapshot = self.get(session, book_id=book_id, sidecar_id=sidecar_id)
        if snapshot is None or snapshot.status != "erased":
            raise RuntimeError("protected content persistence invariant failed")
        return snapshot

    def get_archive_manifest(
        self,
        session: Session,
        *,
        book_id: UUID,
        archive_id: UUID,
    ) -> ImportArchiveManifestSnapshot | None:
        row = session.execute(
            select(ImportArchiveManifestRecord, ProtectedDescriptionSidecarRecord)
            .join(
                ProtectedDescriptionSidecarRecord,
                (
                    ProtectedDescriptionSidecarRecord.book_id
                    == ImportArchiveManifestRecord.book_id
                )
                & (
                    ProtectedDescriptionSidecarRecord.sidecar_id
                    == ImportArchiveManifestRecord.archive_id
                ),
            )
            .where(
                ImportArchiveManifestRecord.book_id == book_id,
                ImportArchiveManifestRecord.archive_id == archive_id,
            )
        ).one_or_none()
        if row is None:
            return None
        return self._archive_snapshot(row[0], row[1])

    def insert_archive_manifest_or_get(
        self,
        session: Session,
        proposed: ProposedImportArchiveManifest,
    ) -> ImportArchiveManifestSnapshot:
        session.execute(
            pg_insert(ImportArchiveManifestRecord)
            .values(
                book_id=proposed.book_id,
                archive_id=proposed.archive_id,
                contract_version=proposed.contract_version,
                source_dump_hash=proposed.source_dump_hash,
                source_manifest_hash=proposed.source_manifest_hash,
                card_review_hash=proposed.card_review_hash,
                plan_hash=proposed.plan_hash,
                archive_content_commitment=proposed.archive_content_commitment,
                seal=proposed.seal,
                record_counts=dict(proposed.record_counts),
            )
            .on_conflict_do_nothing(index_elements=("book_id", "archive_id"))
        )
        result = self.get_archive_manifest(
            session,
            book_id=proposed.book_id,
            archive_id=proposed.archive_id,
        )
        if result is None:
            raise RuntimeError("import archive manifest persistence failed")
        return result

    def list_archive_manifests(
        self,
        session: Session,
        *,
        book_id: UUID,
    ) -> tuple[ImportArchiveManifestSnapshot, ...]:
        rows = session.execute(
            select(ImportArchiveManifestRecord, ProtectedDescriptionSidecarRecord)
            .join(
                ProtectedDescriptionSidecarRecord,
                (
                    ProtectedDescriptionSidecarRecord.book_id
                    == ImportArchiveManifestRecord.book_id
                )
                & (
                    ProtectedDescriptionSidecarRecord.sidecar_id
                    == ImportArchiveManifestRecord.archive_id
                ),
            )
            .where(ImportArchiveManifestRecord.book_id == book_id)
            .order_by(ImportArchiveManifestRecord.archive_id)
        ).all()
        return tuple(self._archive_snapshot(row[0], row[1]) for row in rows)

    @staticmethod
    def _snapshot(
        record: ProtectedDescriptionSidecarRecord,
    ) -> ProtectedContentSnapshot:
        return ProtectedContentSnapshot(
            book_id=record.book_id,
            sidecar_id=record.sidecar_id,
            kind=record.kind,
            ciphertext=record.ciphertext,
            key_ref=record.key_ref,
            nonce=record.nonce,
            algorithm=record.algorithm,
            content_hash=record.content_hash,
            status=record.status,
            created_at=record.created_at,
            erased_at=record.erased_at,
        )

    @classmethod
    def _archive_snapshot(
        cls,
        record: ImportArchiveManifestRecord,
        sidecar: ProtectedDescriptionSidecarRecord,
    ) -> ImportArchiveManifestSnapshot:
        return ImportArchiveManifestSnapshot(
            book_id=record.book_id,
            archive_id=record.archive_id,
            contract_version=record.contract_version,
            source_dump_hash=record.source_dump_hash,
            source_manifest_hash=record.source_manifest_hash,
            card_review_hash=record.card_review_hash,
            plan_hash=record.plan_hash,
            archive_content_commitment=record.archive_content_commitment,
            seal=record.seal,
            record_counts=MappingProxyType(dict(record.record_counts)),
            created_at=record.created_at,
            sidecar=cls._snapshot(sidecar),
        )


__all__ = [
    "ProposedProtectedContent",
    "ProposedImportArchiveManifest",
    "ImportArchiveManifestSnapshot",
    "ProtectedContentNotFound",
    "ProtectedContentRepository",
    "ProtectedContentSnapshot",
]
