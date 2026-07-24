from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ...application.entries.contracts import (
    CommitEntryInput,
    CommittedEntry,
    PreparedEntry,
)
from ...application.entries.errors import EntryErrorCode, EntryGatewayError
from ...application.entries.service import RequestScopedEverydayEntryService
from ...application.idempotency import IdempotencyConflict
from ...application.ledger_committer import BookWritePaused, LedgerCommitter
from ...application.privacy.service import ProtectedContentService
from ...infrastructure.crypto import (
    DuplicateDetectionKeyProvider,
    ProtectedContentCipher,
)
from ...infrastructure.db.repositories.privacy import ProtectedContentRepository
from ...queries.everyday_entries import (
    DecodedTransactionNarrative,
    NarrativeAccess,
    NarrativeStatus,
    TransactionNarrativeDecoder,
    get_everyday_entry,
)
from ...queries.protected_content import (
    ProtectedContentErased,
    ProtectedContentUnavailable,
    get_transaction_narratives,
)
from ..dependencies import SessionDependency, UnitOfWorkFactory
from .entry_schemas import (
    EntryErrorResponse,
    EverydayEntryReceiptResponse,
    PrepareEntryRequest,
)
from .query_routes.authorization import (
    authorize_book_owner_read,
    authorize_book_read,
)
from .schemas import (
    RequestActor,
    create_actor_dependency,
    require_idempotency_key,
)


class CipherBackedTransactionNarrativeDecoder(TransactionNarrativeDecoder):
    """Request-scoped adapter from encrypted sidecars to the semantic read seam."""

    def __init__(
        self,
        session: Session,
        cipher: ProtectedContentCipher,
    ) -> None:
        self._session = session
        self._cipher = cipher

    def decode(
        self,
        *,
        book_id: UUID,
        sidecar_ids: tuple[UUID, ...],
    ) -> Mapping[UUID, DecodedTransactionNarrative]:
        try:
            narratives = get_transaction_narratives(
                self._session,
                book_id,
                narrative_refs=sidecar_ids,
                cipher=self._cipher,
            )
        except ProtectedContentErased:
            return {
                sidecar_id: DecodedTransactionNarrative(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    status=NarrativeStatus.ERASED,
                )
                for sidecar_id in sidecar_ids
            }
        except ProtectedContentUnavailable:
            return {
                sidecar_id: DecodedTransactionNarrative(
                    book_id=book_id,
                    sidecar_id=sidecar_id,
                    status=NarrativeStatus.UNAVAILABLE,
                )
                for sidecar_id in sidecar_ids
            }
        return {
            sidecar_id: DecodedTransactionNarrative(
                book_id=book_id,
                sidecar_id=sidecar_id,
                status=NarrativeStatus.AVAILABLE,
                merchant=narrative.merchant,
                channel=narrative.channel,
            )
            for sidecar_id, narrative in narratives.items()
        }


def create_entry_router(
    *,
    get_session: SessionDependency,
    uow_factory: UnitOfWorkFactory,
    ledger_committer: LedgerCommitter,
    protected_content_cipher: ProtectedContentCipher | None,
    duplicate_key_provider: DuplicateDetectionKeyProvider | None,
) -> APIRouter:
    router = APIRouter(tags=["everyday-entries"])
    request_actor = create_actor_dependency(get_session)
    protected_service = (
        None
        if protected_content_cipher is None
        else ProtectedContentService(
            cipher=protected_content_cipher,
            repository=ProtectedContentRepository(),
        )
    )

    def service(actor: RequestActor) -> RequestScopedEverydayEntryService:
        return RequestScopedEverydayEntryService(
            actor=actor.command_actor,
            uow_factory=uow_factory,
            ledger_committer=ledger_committer,
            protected_content_service=protected_service,
            duplicate_key_provider=duplicate_key_provider,
        )

    @router.post(
        "/books/{book_id}/entries/prepare",
        response_model=PreparedEntry,
        responses=_error_responses(),
    )
    def prepare(
        book_id: UUID,
        payload: PrepareEntryRequest,
        actor: RequestActor = Depends(request_actor),
    ) -> PreparedEntry:
        actor.require_book_scope(book_id, "ledger:write")
        return call_entry_application(
            lambda: service(actor).prepare(book_id=book_id, entry=payload)
        )

    @router.post(
        "/books/{book_id}/entries/commit",
        response_model=CommittedEntry,
        status_code=201,
        responses=_error_responses(),
    )
    def commit(
        book_id: UUID,
        payload: CommitEntryInput,
        actor: RequestActor = Depends(request_actor),
        raw_key: str = Depends(require_idempotency_key),
    ) -> CommittedEntry:
        actor.require_book_scope(book_id, "ledger:write")
        try:
            header_request_id = UUID(raw_key)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": EntryErrorCode.REQUEST_CONFLICT.value,
                    "message": "idempotency header must equal request_id",
                    "field": "request_id",
                    "retryable": False,
                },
            ) from None
        if header_request_id != payload.request_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": EntryErrorCode.REQUEST_CONFLICT.value,
                    "message": "idempotency header must equal request_id",
                    "field": "request_id",
                    "retryable": False,
                },
            )
        return call_entry_application(
            lambda: service(actor).commit(book_id=book_id, command=payload)
        )

    @router.get(
        "/books/{book_id}/entries/{transaction_id}",
        response_model=EverydayEntryReceiptResponse,
        responses=_error_responses(),
    )
    def receipt(
        book_id: UUID,
        transaction_id: UUID,
        request: Request,
        include_narrative: bool = Query(default=False),
        as_of_book_position: int | None = Query(default=None, ge=0),
        session: Session = Depends(get_session),
    ) -> EverydayEntryReceiptResponse:
        authorize_book_read(session, request, book_id)
        decoder = None
        access = NarrativeAccess.REDACTED
        if include_narrative:
            authorize_book_owner_read(session, request, book_id)
            if protected_content_cipher is None:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": EntryErrorCode.UNSUPPORTED.value,
                        "message": "protected narrative storage is unavailable",
                        "field": None,
                        "retryable": True,
                    },
                )
            decoder = CipherBackedTransactionNarrativeDecoder(
                session,
                protected_content_cipher,
            )
            access = NarrativeAccess.OWNER_AUTHORIZED
        try:
            view = get_everyday_entry(
                session,
                book_id,
                transaction_id,
                as_of_book_position=as_of_book_position,
                narrative_access=access,
                narrative_decoder=decoder,
            )
        except LookupError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": EntryErrorCode.INTENT_NOT_FOUND.value,
                    "message": "entry receipt was not found",
                    "field": None,
                    "retryable": False,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": EntryErrorCode.INVALID_INPUT.value,
                    "message": str(error),
                    "field": None,
                    "retryable": False,
                },
            ) from error
        return EverydayEntryReceiptResponse.from_view(view)

    return router


def call_entry_application(action):
    try:
        return action()
    except EntryGatewayError as error:
        raise HTTPException(
            status_code=_entry_status(error),
            detail={
                "code": error.code.value,
                "message": str(error),
                "field": error.field,
                "retryable": error.retryable,
            },
        ) from error
    except IdempotencyConflict as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": EntryErrorCode.REQUEST_CONFLICT.value,
                "message": "request identity conflicts with an earlier request",
                "field": "request_id",
                "retryable": False,
            },
        ) from error
    except (BookWritePaused, IntegrityError) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": EntryErrorCode.BOOK_WRITE_BLOCKED.value,
                "message": "Book cannot accept this entry",
                "field": None,
                "retryable": False,
            },
        ) from error
    except PermissionError as error:
        raise HTTPException(
            status_code=403,
            detail="request is not authorized",
        ) from error
    except DBAPIError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": EntryErrorCode.COMMIT_OUTCOME_UNKNOWN.value,
                "message": "entry commit outcome is unknown",
                "field": None,
                "retryable": True,
            },
        ) from error


def _entry_status(error: EntryGatewayError) -> int:
    if error.code in {
        EntryErrorCode.INTENT_NOT_FOUND,
        EntryErrorCode.ACCOUNT_NOT_FOUND,
        EntryErrorCode.CATEGORY_NOT_FOUND,
        EntryErrorCode.ORIGINAL_TRANSACTION_NOT_FOUND,
        EntryErrorCode.COMMIT_TOKEN_INVALID,
    }:
        return 404
    if error.code is EntryErrorCode.INTENT_EXPIRED:
        return 410
    if error.code in {
        EntryErrorCode.DUPLICATE_SUSPECTED,
        EntryErrorCode.INTENT_NOT_READY,
        EntryErrorCode.INTENT_STALE,
        EntryErrorCode.REQUEST_CONFLICT,
        EntryErrorCode.BOOK_WRITE_BLOCKED,
    }:
        return 409
    if error.code is EntryErrorCode.COMMIT_OUTCOME_UNKNOWN or error.retryable:
        return 503
    return 422


def _error_responses() -> dict[int | str, dict[str, object]]:
    return {
        400: {"model": EntryErrorResponse},
        403: {"description": "Request is not authorized"},
        404: {"model": EntryErrorResponse},
        409: {"model": EntryErrorResponse},
        410: {"model": EntryErrorResponse},
        422: {"model": EntryErrorResponse},
        503: {"model": EntryErrorResponse},
    }


__all__ = [
    "CipherBackedTransactionNarrativeDecoder",
    "call_entry_application",
    "create_entry_router",
]
