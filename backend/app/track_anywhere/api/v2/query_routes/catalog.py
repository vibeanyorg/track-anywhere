from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ....queries.balances import BalanceItem
from ....queries.catalogs import (
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
from ...dependencies import SessionDependency
from ..schemas import authenticate_request_actor
from .authorization import (
    BOOK_READ_SCOPE,
    AuthorizedSessionDependency,
    book_access_denied,
)


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


def create_catalog_query_router(
    get_session: SessionDependency,
    authorized_session: AuthorizedSessionDependency,
) -> APIRouter:
    router = APIRouter()

    @router.get("/books", response_model=BookListResponse)
    def books(
        request: Request,
        session: Session = Depends(get_session),
    ) -> BookListResponse:
        identity = authenticate_request_actor(session, request)
        if BOOK_READ_SCOPE not in identity.scopes:
            raise book_access_denied()
        values = list_accessible_books(
            session,
            user_id=identity.command_actor.subject_id,
            restricted_book_id=identity.credential_book_id,
        )
        return BookListResponse(items=tuple(serialize_book(item) for item in values))

    @router.get("/books/{book_id}/assets", response_model=AssetListResponse)
    def assets(
        book_id: UUID,
        session: Session = Depends(authorized_session),
    ) -> AssetListResponse:
        try:
            values = list_assets(session, book_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Book not found") from error
        return AssetListResponse(items=tuple(serialize_asset(item) for item in values))

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
            items=tuple(serialize_account(item) for item in values)
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
        return read_account(session, book_id, account_id)

    @router.get(
        "/books/{book_id}/accounts/{account_id}/balance",
        response_model=BalanceItemResponse,
    )
    def account_balance(
        book_id: UUID,
        account_id: UUID,
        session: Session = Depends(authorized_session),
    ) -> BalanceItemResponse:
        return read_account(session, book_id, account_id).balance

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
            items=tuple(serialize_category(item) for item in values)
        )

    return router


def read_account(
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
    return serialize_account(value)


def serialize_book(value: BookSummary) -> BookResponse:
    return BookResponse(
        book_id=value.book_id,
        current_name=value.current_name,
        base_asset_code=value.base_asset_code,
        write_state=value.write_state,
    )


def serialize_asset(value: AssetSummary) -> AssetResponse:
    return AssetResponse(
        asset_code=value.asset_code,
        kind=value.kind,
        ledger_scale=value.ledger_scale,
        input_scale=value.input_scale,
        display_scale=value.display_scale,
        current_name=value.current_name,
        status=value.status,
    )


def serialize_account(value: AccountSummary) -> AccountResponse:
    return AccountResponse(
        account_id=value.account_id,
        asset_code=value.asset_code,
        account_type=value.account_type.value,
        account_subtype=value.account_subtype,
        system_role=value.system_role,
        current_name=value.current_name,
        status=value.status,
        balance=serialize_balance_item(value.balance),
    )


def serialize_category(value: CategorySummary) -> CategoryResponse:
    return CategoryResponse(
        category_id=value.category_id,
        parent_category_id=value.parent_category_id,
        current_version_id=value.current_version_id,
        current_name=value.current_name,
        status=value.status,
    )


def serialize_balance_item(item: BalanceItem) -> BalanceItemResponse:
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


__all__ = [
    "AccountListResponse",
    "AccountResponse",
    "AssetListResponse",
    "AssetResponse",
    "BalanceItemResponse",
    "BookListResponse",
    "BookResponse",
    "CategoryListResponse",
    "CategoryResponse",
    "create_catalog_query_router",
    "read_account",
    "serialize_account",
    "serialize_asset",
    "serialize_balance_item",
    "serialize_book",
    "serialize_category",
]
