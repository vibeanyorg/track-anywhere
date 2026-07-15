from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ....queries.journal import (
    InvalidJournalCursor,
    JournalItem,
    JournalPage,
    get_journal_transaction,
    list_journal,
)
from ....serialization.canonical_json import format_utc_microseconds
from .authorization import AuthorizedSessionDependency


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


def create_journal_query_router(
    authorized_session: AuthorizedSessionDependency,
) -> APIRouter:
    router = APIRouter()

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
        return serialize_journal_page(page)

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
        return serialize_journal_item(item)

    return router


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
    "JournalPageResponse",
    "JournalPostingResponse",
    "create_journal_query_router",
    "serialize_journal_item",
    "serialize_journal_page",
]
