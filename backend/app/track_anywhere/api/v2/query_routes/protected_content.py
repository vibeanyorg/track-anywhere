from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ....infrastructure.crypto import ProtectedContentCipher
from ....queries.protected_content import (
    ImportArchiveMetadata,
    ProtectedContentErased,
    ProtectedContentUnavailable,
    export_import_archive,
    list_import_archives,
)
from ....serialization.canonical_json import format_utc_microseconds
from .authorization import AuthorizedSessionDependency


class ImportArchiveRecordCountsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    classification_audit_records: int
    investment_activities: int
    investment_valuations: int
    uncategorized_fx_reporting_facts: int
    institution_metadata_records: int
    counterparty_records: int
    omission_records: int


class ImportArchiveMetadataResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    archive_id: UUID
    contract_version: int
    source_dump_hash: str
    source_manifest_hash: str
    card_review_hash: str
    plan_hash: str
    content_commitment: str
    seal: str
    record_counts: ImportArchiveRecordCountsResponse
    created_at: str


class ImportArchiveListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ImportArchiveMetadataResponse, ...]


class ImportArchiveExportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    archive_id: UUID
    content_type: Literal["application/x-ndjson"]
    content_commitment: str
    seal: str
    ndjson: str


def create_protected_content_query_router(
    owner_authorized_session: AuthorizedSessionDependency,
    *,
    protected_content_cipher: ProtectedContentCipher | None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/books/{book_id}/import-archives",
        response_model=ImportArchiveListResponse,
    )
    def import_archives(
        book_id: UUID,
        session: Session = Depends(owner_authorized_session),
    ) -> ImportArchiveListResponse:
        cipher = _require_cipher(protected_content_cipher)
        try:
            archives = list_import_archives(session, book_id, cipher=cipher)
        except ProtectedContentErased as error:
            raise _erased() from error
        except ProtectedContentUnavailable as error:
            raise _unavailable() from error
        return ImportArchiveListResponse(
            items=tuple(_serialize_metadata(archive) for archive in archives)
        )

    @router.get(
        "/books/{book_id}/import-archives/{archive_id}/export",
        response_model=ImportArchiveExportResponse,
    )
    def import_archive_export(
        book_id: UUID,
        archive_id: UUID,
        session: Session = Depends(owner_authorized_session),
    ) -> ImportArchiveExportResponse:
        cipher = _require_cipher(protected_content_cipher)
        try:
            archive = export_import_archive(
                session,
                book_id,
                archive_id,
                cipher=cipher,
            )
        except LookupError as error:
            raise HTTPException(
                status_code=404,
                detail="Import archive not found",
            ) from error
        except ProtectedContentErased as error:
            raise _erased() from error
        except ProtectedContentUnavailable as error:
            raise _unavailable() from error
        try:
            ndjson = archive.canonical_ndjson.decode("utf-8")
        except UnicodeError:
            raise _unavailable() from None
        return ImportArchiveExportResponse(
            archive_id=archive.archive_id,
            content_type="application/x-ndjson",
            content_commitment=archive.content_commitment.hex(),
            seal=archive.seal.hex(),
            ndjson=ndjson,
        )

    return router


def _require_cipher(
    cipher: ProtectedContentCipher | None,
) -> ProtectedContentCipher:
    if cipher is None:
        raise _unavailable()
    return cipher


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Protected content is unavailable",
    )


def _erased() -> HTTPException:
    return HTTPException(
        status_code=410,
        detail="Protected content was erased",
    )


def _serialize_metadata(
    archive: ImportArchiveMetadata,
) -> ImportArchiveMetadataResponse:
    return ImportArchiveMetadataResponse(
        archive_id=archive.archive_id,
        contract_version=archive.contract_version,
        source_dump_hash=archive.source_dump_hash.hex(),
        source_manifest_hash=archive.source_manifest_hash.hex(),
        card_review_hash=archive.card_review_hash.hex(),
        plan_hash=archive.plan_hash.hex(),
        content_commitment=archive.content_commitment.hex(),
        seal=archive.seal.hex(),
        record_counts=ImportArchiveRecordCountsResponse.model_validate(
            dict(archive.record_counts)
        ),
        created_at=format_utc_microseconds(archive.created_at),
    )


__all__ = [
    "ImportArchiveExportResponse",
    "ImportArchiveListResponse",
    "ImportArchiveMetadataResponse",
    "ImportArchiveRecordCountsResponse",
    "create_protected_content_query_router",
]
