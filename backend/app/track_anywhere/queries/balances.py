from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.event_store import BookEventHeadRecord
from ..infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)


@dataclass(frozen=True, slots=True)
class BalanceItem:
    account_id: UUID
    asset_code: str
    units: int


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    items: tuple[BalanceItem, ...]
    as_of_book_position: int
    projection_matches_reference: bool | None


def get_book_balances(
    session: Session,
    book_id: UUID,
    *,
    as_of_book_position: int | None = None,
) -> BalanceSnapshot:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    head = session.scalar(
        select(BookEventHeadRecord.last_position).where(
            BookEventHeadRecord.book_id == book_id
        )
    )
    if head is None:
        raise LookupError("Book not found")
    as_of = head if as_of_book_position is None else as_of_book_position
    if type(as_of) is not int or not 0 <= as_of <= head:
        raise ValueError("as_of_book_position is outside the Book head")

    signed_units = case(
        (JournalPostingRecord.side == "debit", JournalPostingRecord.units),
        else_=-JournalPostingRecord.units,
    )
    reference_rows = session.execute(
        select(
            JournalPostingRecord.account_id,
            JournalPostingRecord.asset_code,
            func.sum(signed_units),
        )
        .join(
            JournalTransactionRecord,
            (JournalTransactionRecord.book_id == JournalPostingRecord.book_id)
            & (
                JournalTransactionRecord.transaction_id
                == JournalPostingRecord.transaction_id
            ),
        )
        .where(
            JournalPostingRecord.book_id == book_id,
            JournalTransactionRecord.source_position <= as_of,
        )
        .group_by(JournalPostingRecord.account_id, JournalPostingRecord.asset_code)
    )
    reference = {
        (account_id, asset_code): int(units)
        for account_id, asset_code, units in reference_rows
    }
    parity: bool | None = None
    values = reference
    if as_of == head:
        projection = {
            (row.account_id, row.asset_code): int(row.balance_units)
            for row in session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == book_id
                )
            )
        }
        parity = projection == reference
        values = projection
    items = tuple(
        BalanceItem(account_id=account_id, asset_code=asset_code, units=units)
        for (account_id, asset_code), units in sorted(
            values.items(), key=lambda item: (str(item[0][0]), item[0][1])
        )
    )
    return BalanceSnapshot(
        items=items,
        as_of_book_position=as_of,
        projection_matches_reference=parity,
    )


__all__ = ["BalanceItem", "BalanceSnapshot", "get_book_balances"]
