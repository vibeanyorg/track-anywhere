from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ...auth.errors import AuthPolicyDenied
from ...auth.http import SESSION_COOKIE
from ...auth.oauth import PersistentOAuthService
from ...auth.sessions import PersistentSessionService
from ...infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    AuthRepository,
)
from ...queries.balances import BalanceSnapshot, get_book_balances
from ...queries.journal import InvalidJournalCursor, JournalPage, list_journal
from ...queries.reporting import ReportingLine, list_current_reporting_lines
from ...serialization.canonical_json import format_utc_microseconds
from ..dependencies import SessionDependency


BOOK_READ_SCOPE = "ledger:read"
BookReadAuthorizer = Callable[[Session, Request, UUID], None]


class JournalPostingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    posting_id: UUID
    position: int
    account_id: UUID
    asset_code: str
    side: str
    units: str


class JournalItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaction_id: UUID
    effective_at: str
    book_position: int
    transaction_kind: str
    postings: tuple[JournalPostingResponse, ...]
    is_reversed: bool
    reversed_by_transaction_id: UUID | None
    reverses_transaction_id: UUID | None


class JournalPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[JournalItemResponse, ...]
    next_cursor: str | None
    as_of_book_position: int


class BalanceItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: UUID
    asset_code: str
    units: str


class BalanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[BalanceItemResponse, ...]
    as_of_book_position: int
    projection_matches_reference: bool | None


class ReportingLineResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaction_id: UUID
    classification_revision: int
    line_id: UUID
    line_version_id: UUID
    line_position: int
    asset_code: str
    units: str
    line_kind: str
    dimension: str
    dimension_id: UUID | None


class ReportingLinesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ReportingLineResponse, ...]
    as_of_book_position: int


@dataclass(frozen=True, slots=True)
class _QueryIdentity:
    user_id: str
    book_id: UUID | None
    scopes: tuple[str, ...]


def authorize_book_read(
    session: Session,
    request: Request,
    book_id: UUID,
) -> None:
    identity = _request_identity(session, request)
    if identity.book_id is not None and identity.book_id != book_id:
        raise _book_access_denied()
    if BOOK_READ_SCOPE not in identity.scopes:
        raise _book_access_denied()
    try:
        membership = AuthRepository(session).get_membership(
            book_id,
            identity.user_id,
        )
    except AuthRecordNotFound as error:
        raise _book_access_denied() from error
    if (
        membership.status != "active"
        or membership.revoked_at is not None
        or BOOK_READ_SCOPE not in membership.scopes
    ):
        raise _book_access_denied()


def create_query_router(
    get_session: SessionDependency,
    *,
    authorize_book_read: BookReadAuthorizer = authorize_book_read,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["queries"])

    def authorized_session(
        request: Request,
        book_id: UUID,
        session: Session = Depends(get_session),
    ) -> Session:
        authorize_book_read(session, request, book_id)
        return session

    @router.get(
        "/books/{book_id}/journal",
        response_model=JournalPageResponse,
    )
    def journal(
        book_id: UUID,
        session: Session = Depends(authorized_session),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=256),
        as_of_book_position: int | None = Query(default=None, ge=0),
    ) -> JournalPageResponse:
        try:
            page = list_journal(
                session,
                book_id,
                limit=limit,
                cursor=cursor,
                as_of_book_position=as_of_book_position,
            )
        except InvalidJournalCursor as error:
            raise HTTPException(
                status_code=400,
                detail="journal cursor is invalid",
            ) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _serialize_journal_page(page)

    @router.get(
        "/books/{book_id}/balances",
        response_model=BalanceSnapshotResponse,
    )
    def balances(
        book_id: UUID,
        session: Session = Depends(authorized_session),
        as_of_book_position: int | None = Query(default=None, ge=0),
    ) -> BalanceSnapshotResponse:
        try:
            snapshot = get_book_balances(
                session,
                book_id,
                as_of_book_position=as_of_book_position,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _serialize_balance_snapshot(snapshot)

    @router.get(
        "/books/{book_id}/reporting-lines",
        response_model=ReportingLinesResponse,
    )
    def reporting_lines(
        book_id: UUID,
        as_of_book_position: int = Query(ge=0),
        session: Session = Depends(authorized_session),
    ) -> ReportingLinesResponse:
        try:
            lines = list_current_reporting_lines(
                session,
                book_id,
                as_of_book_position=as_of_book_position,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _serialize_reporting_lines(lines, as_of_book_position)

    return router


def _request_identity(session: Session, request: Request) -> _QueryIdentity:
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, separator, raw_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not raw_token:
            raise _credential_required()
        try:
            status = PersistentOAuthService(session).token_status(raw_token)
        except AuthPolicyDenied as error:
            raise _credential_required() from error
        return _identity_from_values(
            user_id=status.get("actor_subject_id"),
            book_id=status.get("book_id"),
            scopes=status.get("scopes"),
        )

    active = PersistentSessionService(session).current(
        request.cookies.get(SESSION_COOKIE)
    )
    if active is None:
        raise _credential_required()
    identity = active.identity
    return _identity_from_values(
        user_id=identity.user_id,
        book_id=identity.book_id,
        scopes=identity.scopes,
    )


def _identity_from_values(
    *,
    user_id: object,
    book_id: object,
    scopes: object,
) -> _QueryIdentity:
    if type(user_id) is not str or not user_id.strip():
        raise _credential_required()
    if not isinstance(scopes, (list, tuple)) or not all(
        type(scope) is str and bool(scope.strip()) for scope in scopes
    ):
        raise _credential_required()
    try:
        parsed_book_id = None if book_id is None else UUID(str(book_id))
    except ValueError:
        raise _credential_required() from None
    return _QueryIdentity(
        user_id=user_id,
        book_id=parsed_book_id,
        scopes=tuple(scopes),
    )


def _credential_required() -> HTTPException:
    return HTTPException(status_code=401, detail="A valid credential is required")


def _book_access_denied() -> HTTPException:
    return HTTPException(status_code=403, detail="Book read access is denied")


def _serialize_journal_page(page: JournalPage) -> JournalPageResponse:
    return JournalPageResponse(
        items=tuple(
            JournalItemResponse(
                transaction_id=item.transaction_id,
                effective_at=format_utc_microseconds(item.effective_at),
                book_position=item.book_position,
                transaction_kind=item.transaction_kind,
                postings=tuple(
                    JournalPostingResponse(
                        posting_id=posting.posting_id,
                        position=posting.position,
                        account_id=posting.account_id,
                        asset_code=posting.asset_code,
                        side=posting.side,
                        units=str(posting.units),
                    )
                    for posting in item.postings
                ),
                is_reversed=item.reversed_by_transaction_id is not None,
                reversed_by_transaction_id=item.reversed_by_transaction_id,
                reverses_transaction_id=item.reverses_transaction_id,
            )
            for item in page.items
        ),
        next_cursor=page.next_cursor,
        as_of_book_position=page.as_of_book_position,
    )


def _serialize_balance_snapshot(
    snapshot: BalanceSnapshot,
) -> BalanceSnapshotResponse:
    return BalanceSnapshotResponse(
        items=tuple(
            BalanceItemResponse(
                account_id=item.account_id,
                asset_code=item.asset_code,
                units=str(item.units),
            )
            for item in snapshot.items
        ),
        as_of_book_position=snapshot.as_of_book_position,
        projection_matches_reference=snapshot.projection_matches_reference,
    )


def _serialize_reporting_lines(
    lines: tuple[ReportingLine, ...],
    as_of_book_position: int,
) -> ReportingLinesResponse:
    return ReportingLinesResponse(
        items=tuple(
            ReportingLineResponse(
                transaction_id=line.transaction_id,
                classification_revision=line.classification_revision,
                line_id=line.line_id,
                line_version_id=line.line_version_id,
                line_position=line.line_position,
                asset_code=line.asset_code,
                units=str(line.units),
                line_kind=line.line_kind,
                dimension=line.dimension,
                dimension_id=line.dimension_id,
            )
            for line in lines
        ),
        as_of_book_position=as_of_book_position,
    )


__all__ = [
    "BOOK_READ_SCOPE",
    "BalanceItemResponse",
    "BalanceSnapshotResponse",
    "BookReadAuthorizer",
    "JournalItemResponse",
    "JournalPageResponse",
    "JournalPostingResponse",
    "ReportingLineResponse",
    "ReportingLinesResponse",
    "authorize_book_read",
    "create_query_router",
]
