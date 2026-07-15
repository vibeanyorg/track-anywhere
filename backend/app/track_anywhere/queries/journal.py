from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from ..infrastructure.db.models.credit_cards import CreditCardTransactionRecord
from ..infrastructure.db.models.event_store import BookEventHeadRecord
from ..infrastructure.db.models.projections import (
    JournalPostingRecord,
    JournalTransactionRecord,
    TransactionReversalRecord,
)
from ..serialization.canonical_json import format_utc_microseconds


class InvalidJournalCursor(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JournalPosting:
    posting_id: UUID
    position: int
    account_id: UUID
    asset_code: str
    side: str
    units: int


@dataclass(frozen=True, slots=True)
class CreditCardRelation:
    intent: str
    card_account_id: UUID
    counter_account_id: UUID
    original_transaction_id: UUID | None


@dataclass(frozen=True, slots=True)
class JournalItem:
    transaction_id: UUID
    effective_at: datetime
    book_position: int
    transaction_kind: str
    postings: tuple[JournalPosting, ...]
    reversed_by_transaction_id: UUID | None
    reverses_transaction_id: UUID | None
    credit_card_relation: CreditCardRelation | None = None


@dataclass(frozen=True, slots=True)
class JournalPage:
    items: tuple[JournalItem, ...]
    next_cursor: str | None
    as_of_book_position: int


def list_journal(
    session: Session,
    book_id: UUID,
    *,
    limit: int,
    cursor: str | None = None,
    as_of_book_position: int | None = None,
) -> JournalPage:
    return _list_journal(
        session,
        book_id,
        limit=limit,
        cursor=cursor,
        as_of_book_position=as_of_book_position,
        transaction_id=None,
    )


def get_journal_transaction(
    session: Session,
    book_id: UUID,
    transaction_id: UUID,
    *,
    as_of_book_position: int | None = None,
) -> JournalItem:
    if type(transaction_id) is not UUID:
        raise ValueError("transaction_id must be a UUID")
    page = _list_journal(
        session,
        book_id,
        limit=1,
        cursor=None,
        as_of_book_position=as_of_book_position,
        transaction_id=transaction_id,
    )
    if not page.items:
        raise LookupError("Transaction not found")
    return page.items[0]


def _list_journal(
    session: Session,
    book_id: UUID,
    *,
    limit: int,
    cursor: str | None,
    as_of_book_position: int | None,
    transaction_id: UUID | None,
) -> JournalPage:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    head_position = session.scalar(
        select(BookEventHeadRecord.last_position).where(
            BookEventHeadRecord.book_id == book_id
        )
    )
    if head_position is None:
        raise LookupError("Book not found")
    as_of = head_position if as_of_book_position is None else as_of_book_position
    if type(as_of) is not int or not 0 <= as_of <= head_position:
        raise ValueError("as_of_book_position is outside the Book head")

    reversed_relation = aliased(TransactionReversalRecord)
    reversal_relation = aliased(TransactionReversalRecord)
    reversed_transaction = aliased(JournalTransactionRecord)
    original_transaction = aliased(JournalTransactionRecord)
    statement = (
        select(
            JournalTransactionRecord,
            reversed_transaction.transaction_id,
            original_transaction.transaction_id,
        )
        .outerjoin(
            reversed_relation,
            and_(
                reversed_relation.book_id == JournalTransactionRecord.book_id,
                reversed_relation.original_transaction_id
                == JournalTransactionRecord.transaction_id,
            ),
        )
        .outerjoin(
            reversal_relation,
            and_(
                reversal_relation.book_id == JournalTransactionRecord.book_id,
                reversal_relation.reversal_transaction_id
                == JournalTransactionRecord.transaction_id,
            ),
        )
        .outerjoin(
            reversed_transaction,
            and_(
                reversed_transaction.book_id == reversed_relation.book_id,
                reversed_transaction.transaction_id
                == reversed_relation.reversal_transaction_id,
                reversed_transaction.source_position <= as_of,
            ),
        )
        .outerjoin(
            original_transaction,
            and_(
                original_transaction.book_id == reversal_relation.book_id,
                original_transaction.transaction_id
                == reversal_relation.original_transaction_id,
                original_transaction.source_position <= as_of,
            ),
        )
        .where(
            JournalTransactionRecord.book_id == book_id,
            JournalTransactionRecord.source_position <= as_of,
        )
    )
    if transaction_id is not None:
        statement = statement.where(
            JournalTransactionRecord.transaction_id == transaction_id
        )
    if cursor is not None:
        effective_at, position, transaction_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                JournalTransactionRecord.effective_at > effective_at,
                and_(
                    JournalTransactionRecord.effective_at == effective_at,
                    JournalTransactionRecord.source_position > position,
                ),
                and_(
                    JournalTransactionRecord.effective_at == effective_at,
                    JournalTransactionRecord.source_position == position,
                    JournalTransactionRecord.transaction_id > transaction_id,
                ),
            )
        )
    rows = tuple(
        session.execute(
            statement.order_by(
                JournalTransactionRecord.effective_at,
                JournalTransactionRecord.source_position,
                JournalTransactionRecord.transaction_id,
            ).limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    transaction_ids = tuple(row[0].transaction_id for row in page_rows)
    postings_by_transaction: dict[UUID, list[JournalPosting]] = {
        transaction_id: [] for transaction_id in transaction_ids
    }
    card_relations: dict[UUID, CreditCardRelation] = {}
    if transaction_ids:
        postings = session.scalars(
            select(JournalPostingRecord)
            .where(
                JournalPostingRecord.book_id == book_id,
                JournalPostingRecord.transaction_id.in_(transaction_ids),
            )
            .order_by(
                JournalPostingRecord.transaction_id,
                JournalPostingRecord.posting_position,
            )
        )
        for posting in postings:
            postings_by_transaction[posting.transaction_id].append(
                JournalPosting(
                    posting_id=posting.posting_id,
                    position=posting.posting_position,
                    account_id=posting.account_id,
                    asset_code=posting.asset_code,
                    side=str(posting.side),
                    units=int(posting.units),
                )
            )
        for relation in session.scalars(
            select(CreditCardTransactionRecord).where(
                CreditCardTransactionRecord.book_id == book_id,
                CreditCardTransactionRecord.transaction_id.in_(transaction_ids),
            )
        ):
            card_relations[relation.transaction_id] = CreditCardRelation(
                intent=relation.intent,
                card_account_id=relation.card_account_id,
                counter_account_id=relation.counter_account_id,
                original_transaction_id=relation.original_transaction_id,
            )
    items = tuple(
        JournalItem(
            transaction_id=transaction.transaction_id,
            effective_at=transaction.effective_at,
            book_position=transaction.source_position,
            transaction_kind=transaction.transaction_kind,
            postings=tuple(postings_by_transaction[transaction.transaction_id]),
            reversed_by_transaction_id=reversed_by,
            reverses_transaction_id=reverses,
            credit_card_relation=card_relations.get(transaction.transaction_id),
        )
        for transaction, reversed_by, reverses in page_rows
    )
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(
            last.effective_at,
            last.book_position,
            last.transaction_id,
        )
    return JournalPage(
        items=items,
        next_cursor=next_cursor,
        as_of_book_position=as_of,
    )


def _encode_cursor(effective_at: datetime, position: int, transaction_id: UUID) -> str:
    raw = (
        f"{format_utc_microseconds(effective_at)}\0{position}\0{transaction_id}"
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(value: str) -> tuple[datetime, int, UUID]:
    if type(value) is not str or not value or len(value) > 256:
        raise InvalidJournalCursor("journal cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        effective_raw, position_raw, transaction_raw = decoded.split("\0")
        if not effective_raw.endswith("Z"):
            raise ValueError
        effective_at = datetime.fromisoformat(effective_raw[:-1] + "+00:00")
        if format_utc_microseconds(effective_at) != effective_raw:
            raise ValueError
        position = int(position_raw)
        transaction_id = UUID(transaction_raw)
    except (UnicodeError, ValueError):
        raise InvalidJournalCursor("journal cursor is invalid") from None
    if position < 1:
        raise InvalidJournalCursor("journal cursor is invalid")
    return effective_at, position, transaction_id


__all__ = [
    "InvalidJournalCursor",
    "CreditCardRelation",
    "JournalItem",
    "JournalPage",
    "JournalPosting",
    "get_journal_transaction",
    "list_journal",
]
