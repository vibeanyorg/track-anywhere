from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ....infrastructure.crypto import ProtectedContentCipher
from ....queries.journal import (
    InvalidJournalCursor,
    JournalItem,
    JournalPage,
    get_journal_transaction,
    list_journal,
)
from ....queries.protected_content import (
    ProtectedContentErased,
    ProtectedContentUnavailable,
    get_transaction_descriptions,
)
from ....application.privacy.protected_content import TransactionDescription
from ....serialization.canonical_json import format_utc_microseconds
from .authorization import AuthorizedSessionDependency, BookOwnerReadAuthorizer


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


class TransactionDescriptionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    purpose: str | None
    transaction_memo: str | None
    line_memos: tuple[str | None, ...]


class JournalItemWithDescriptionResponse(JournalItemResponse):
    description: TransactionDescriptionResponse | None = None


class JournalPageWithDescriptionsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[JournalItemWithDescriptionResponse, ...]
    next_cursor: str | None
    as_of_book_position: int


def create_journal_query_router(
    authorized_session: AuthorizedSessionDependency,
    *,
    authorize_book_owner_read: BookOwnerReadAuthorizer,
    protected_content_cipher: ProtectedContentCipher | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/books/{book_id}/journal",
        response_model=JournalPageWithDescriptionsResponse,
        response_model_exclude_unset=True,
    )
    def journal(
        book_id: UUID,
        request: Request,
        session: Session = Depends(authorized_session),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=256),
        as_of_book_position: int | None = Query(default=None, ge=0),
        include_description: bool = Query(default=False),
    ) -> JournalPageWithDescriptionsResponse:
        if include_description:
            authorize_book_owner_read(session, request, book_id)
            _require_cipher(protected_content_cipher)
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
        if not include_description:
            return serialize_journal_page_with_descriptions(page)
        descriptions = _load_descriptions(
            session,
            book_id,
            page.items,
            cipher=protected_content_cipher,
        )
        return serialize_journal_page_with_descriptions(
            page,
            descriptions=descriptions,
        )

    @router.get(
        "/books/{book_id}/journal/transactions/{transaction_id}",
        response_model=JournalItemWithDescriptionResponse,
        response_model_exclude_unset=True,
    )
    def journal_transaction(
        book_id: UUID,
        transaction_id: UUID,
        request: Request,
        as_of_book_position: int | None = Query(default=None, ge=0),
        include_description: bool = Query(default=False),
        session: Session = Depends(authorized_session),
    ) -> JournalItemWithDescriptionResponse:
        if include_description:
            authorize_book_owner_read(session, request, book_id)
            _require_cipher(protected_content_cipher)
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
        if not include_description:
            return serialize_journal_item_with_description(item)
        descriptions = _load_descriptions(
            session,
            book_id,
            (item,),
            cipher=protected_content_cipher,
        )
        return serialize_journal_item_with_description(
            item,
            description=(
                None
                if item.description_ref is None
                else descriptions[item.description_ref]
            ),
        )

    return router


_DESCRIPTION_UNSET = object()


def _require_cipher(
    cipher: ProtectedContentCipher | None,
) -> ProtectedContentCipher:
    if cipher is None:
        raise HTTPException(
            status_code=503,
            detail="Protected content is unavailable",
        )
    return cipher


def _load_descriptions(
    session: Session,
    book_id: UUID,
    items: tuple[JournalItem, ...],
    *,
    cipher: ProtectedContentCipher | None,
) -> dict[UUID, TransactionDescription]:
    description_refs = tuple(
        item.description_ref for item in items if item.description_ref is not None
    )
    if not description_refs:
        return {}
    try:
        return get_transaction_descriptions(
            session,
            book_id,
            description_refs=description_refs,
            cipher=_require_cipher(cipher),
        )
    except ProtectedContentErased as error:
        raise HTTPException(
            status_code=410,
            detail="Protected content was erased",
        ) from error
    except ProtectedContentUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail="Protected content is unavailable",
        ) from error


def serialize_journal_page_with_descriptions(
    page: JournalPage,
    *,
    descriptions: dict[UUID, TransactionDescription] | None = None,
) -> JournalPageWithDescriptionsResponse:
    return JournalPageWithDescriptionsResponse(
        items=tuple(
            serialize_journal_item_with_description(
                item,
                description=(
                    _DESCRIPTION_UNSET
                    if descriptions is None
                    else None
                    if item.description_ref is None
                    else descriptions[item.description_ref]
                ),
            )
            for item in page.items
        ),
        next_cursor=page.next_cursor,
        as_of_book_position=page.as_of_book_position,
    )


def serialize_journal_item_with_description(
    item: JournalItem,
    *,
    description: TransactionDescription | None | object = _DESCRIPTION_UNSET,
) -> JournalItemWithDescriptionResponse:
    values = serialize_journal_item(item).model_dump(mode="python")
    if description is _DESCRIPTION_UNSET:
        return JournalItemWithDescriptionResponse(**values)
    rendered = (
        None
        if description is None
        else TransactionDescriptionResponse.model_validate(
            description.model_dump(mode="python")
        )
    )
    return JournalItemWithDescriptionResponse(**values, description=rendered)


def serialize_journal_page(page: JournalPage) -> JournalPageResponse:
    return JournalPageResponse(
        items=tuple(serialize_journal_item(item) for item in page.items),
        next_cursor=page.next_cursor,
        as_of_book_position=page.as_of_book_position,
    )


def serialize_journal_item(item: JournalItem) -> JournalItemResponse:
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


__all__ = [
    "CreditCardRelationResponse",
    "JournalItemResponse",
    "JournalItemWithDescriptionResponse",
    "JournalPageResponse",
    "JournalPageWithDescriptionsResponse",
    "JournalPostingResponse",
    "TransactionDescriptionResponse",
    "create_journal_query_router",
    "serialize_journal_item",
    "serialize_journal_page",
]
