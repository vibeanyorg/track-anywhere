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
from ...queries.balances import BalanceItem, BalanceSnapshot, get_book_balances
from ...queries.catalogs import (
    AccountSummary,
    AssetSummary,
    BookSummary,
    CategorySummary,
    get_account,
    list_accessible_books,
    list_accounts,
    list_assets,
    list_categories,
)
from ...queries.journal import (
    InvalidJournalCursor,
    JournalItem,
    JournalPage,
    get_journal_transaction,
    list_journal,
)
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


class CreditCardRelationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: str
    card_account_id: UUID
    counter_account_id: UUID
    original_transaction_id: UUID | None


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
    credit_card_relation: CreditCardRelationResponse | None


class JournalPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[JournalItemResponse, ...]
    next_cursor: str | None
    as_of_book_position: int


class BalanceItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: UUID
    asset_code: str
    account_type: str
    account_subtype: str | None
    account_status: str
    raw_accounting_units: str
    natural_units: str
    normal_side: str
    balance_semantics: str
    outstanding_units: str | None
    overpayment_units: str | None


class BalanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[BalanceItemResponse, ...]
    as_of_book_position: int
    projection_matches_reference: bool | None


class BookResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    book_id: UUID
    current_name: str
    base_asset_code: str | None
    write_state: str


class BookListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[BookResponse, ...]


class AssetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_code: str
    kind: str
    ledger_scale: int
    input_scale: int
    display_scale: int
    current_name: str
    status: str


class AssetListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AssetResponse, ...]


class AccountResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: UUID
    asset_code: str
    account_type: str
    account_subtype: str | None
    system_role: str | None
    current_name: str
    status: str
    balance: BalanceItemResponse


class AccountListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[AccountResponse, ...]


class CategoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    category_id: UUID
    parent_category_id: UUID | None
    current_version_id: UUID | None
    current_name: str
    status: str


class CategoryListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[CategoryResponse, ...]


class ReportingLineResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaction_id: UUID
    classification_revision: int
    line_id: UUID
    line_version_id: UUID
    catalog_id: UUID
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

    @router.get("/books", response_model=BookListResponse)
    def books(
        request: Request,
        session: Session = Depends(get_session),
    ) -> BookListResponse:
        identity = _request_identity(session, request)
        if BOOK_READ_SCOPE not in identity.scopes:
            raise _book_access_denied()
        values = list_accessible_books(
            session,
            user_id=identity.user_id,
            restricted_book_id=identity.book_id,
        )
        return BookListResponse(items=tuple(_serialize_book(item) for item in values))

    @router.get("/books/{book_id}/assets", response_model=AssetListResponse)
    def assets(
        book_id: UUID,
        session: Session = Depends(authorized_session),
    ) -> AssetListResponse:
        try:
            values = list_assets(session, book_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        return AssetListResponse(items=tuple(_serialize_asset(item) for item in values))

    @router.get("/books/{book_id}/accounts", response_model=AccountListResponse)
    def accounts(
        book_id: UUID,
        account_type: str | None = Query(default=None, max_length=32),
        account_subtype: str | None = Query(default=None, max_length=64),
        status: str | None = Query(default=None, max_length=16),
        asset_code: str | None = Query(default=None, max_length=16),
        name: str | None = Query(default=None, max_length=128),
        session: Session = Depends(authorized_session),
    ) -> AccountListResponse:
        try:
            values = list_accounts(
                session,
                book_id,
                account_type=account_type,
                account_subtype=account_subtype,
                status=status,
                asset_code=asset_code,
                name=name,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return AccountListResponse(
            items=tuple(_serialize_account(item) for item in values)
        )

    @router.get(
        "/books/{book_id}/accounts/{account_id}",
        response_model=AccountResponse,
    )
    def account(
        book_id: UUID,
        account_id: UUID,
        session: Session = Depends(authorized_session),
    ) -> AccountResponse:
        return _read_account(session, book_id, account_id)

    @router.get(
        "/books/{book_id}/accounts/{account_id}/balance",
        response_model=BalanceItemResponse,
    )
    def account_balance(
        book_id: UUID,
        account_id: UUID,
        session: Session = Depends(authorized_session),
    ) -> BalanceItemResponse:
        return _read_account(session, book_id, account_id).balance

    @router.get("/books/{book_id}/categories", response_model=CategoryListResponse)
    def categories(
        book_id: UUID,
        session: Session = Depends(authorized_session),
    ) -> CategoryListResponse:
        try:
            values = list_categories(session, book_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        return CategoryListResponse(
            items=tuple(_serialize_category(item) for item in values)
        )

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
        "/books/{book_id}/journal/transactions/{transaction_id}",
        response_model=JournalItemResponse,
    )
    def journal_transaction(
        book_id: UUID,
        transaction_id: UUID,
        as_of_book_position: int | None = Query(default=None, ge=0),
        session: Session = Depends(authorized_session),
    ) -> JournalItemResponse:
        try:
            item = get_journal_transaction(
                session,
                book_id,
                transaction_id,
                as_of_book_position=as_of_book_position,
            )
        except LookupError as error:
            detail = (
                "Transaction not found"
                if str(error) == "Transaction not found"
                else "Book not found"
            )
            raise HTTPException(status_code=404, detail=detail) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _serialize_journal_item(item)

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


def _read_account(
    session: Session,
    book_id: UUID,
    account_id: UUID,
) -> AccountResponse:
    try:
        value = get_account(session, book_id, account_id)
    except LookupError as error:
        detail = (
            "Account not found"
            if str(error) == "Account not found"
            else "Book not found"
        )
        raise HTTPException(status_code=404, detail=detail) from error
    return _serialize_account(value)


def _serialize_book(value: BookSummary) -> BookResponse:
    return BookResponse(
        book_id=value.book_id,
        current_name=value.current_name,
        base_asset_code=value.base_asset_code,
        write_state=value.write_state,
    )


def _serialize_asset(value: AssetSummary) -> AssetResponse:
    return AssetResponse(
        asset_code=value.asset_code,
        kind=value.kind,
        ledger_scale=value.ledger_scale,
        input_scale=value.input_scale,
        display_scale=value.display_scale,
        current_name=value.current_name,
        status=value.status,
    )


def _serialize_account(value: AccountSummary) -> AccountResponse:
    return AccountResponse(
        account_id=value.account_id,
        asset_code=value.asset_code,
        account_type=value.account_type.value,
        account_subtype=value.account_subtype,
        system_role=value.system_role,
        current_name=value.current_name,
        status=value.status,
        balance=_serialize_balance_item(value.balance),
    )


def _serialize_category(value: CategorySummary) -> CategoryResponse:
    return CategoryResponse(
        category_id=value.category_id,
        parent_category_id=value.parent_category_id,
        current_version_id=value.current_version_id,
        current_name=value.current_name,
        status=value.status,
    )


def _serialize_balance_item(item: BalanceItem) -> BalanceItemResponse:
    return BalanceItemResponse(
        account_id=item.account_id,
        asset_code=item.asset_code,
        account_type=item.account_type.value,
        account_subtype=item.account_subtype,
        account_status=item.account_status,
        raw_accounting_units=str(item.raw_accounting_units),
        natural_units=str(item.natural_units),
        normal_side=item.normal_side.value,
        balance_semantics=item.balance_semantics,
        outstanding_units=(
            None if item.outstanding_units is None else str(item.outstanding_units)
        ),
        overpayment_units=(
            None if item.overpayment_units is None else str(item.overpayment_units)
        ),
    )


def _serialize_journal_page(page: JournalPage) -> JournalPageResponse:
    return JournalPageResponse(
        items=tuple(_serialize_journal_item(item) for item in page.items),
        next_cursor=page.next_cursor,
        as_of_book_position=page.as_of_book_position,
    )


def _serialize_journal_item(item: JournalItem) -> JournalItemResponse:
    return JournalItemResponse(
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
        credit_card_relation=(
            None
            if item.credit_card_relation is None
            else CreditCardRelationResponse(
                intent=item.credit_card_relation.intent,
                card_account_id=item.credit_card_relation.card_account_id,
                counter_account_id=item.credit_card_relation.counter_account_id,
                original_transaction_id=item.credit_card_relation.original_transaction_id,
            )
        ),
    )


def _serialize_balance_snapshot(
    snapshot: BalanceSnapshot,
) -> BalanceSnapshotResponse:
    return BalanceSnapshotResponse(
        items=tuple(_serialize_balance_item(item) for item in snapshot.items),
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
                catalog_id=line.catalog_id,
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
    "AccountListResponse",
    "AccountResponse",
    "AssetListResponse",
    "AssetResponse",
    "BookListResponse",
    "BookResponse",
    "BookReadAuthorizer",
    "JournalItemResponse",
    "JournalPageResponse",
    "JournalPostingResponse",
    "CategoryListResponse",
    "CategoryResponse",
    "ReportingLineResponse",
    "ReportingLinesResponse",
    "authorize_book_read",
    "create_query_router",
]
