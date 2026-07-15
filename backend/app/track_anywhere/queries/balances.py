from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..domain.journal import AccountType, PostingSide
from ..infrastructure.db.models.catalog import AccountRecord
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
    account_type: AccountType
    account_subtype: str | None
    account_status: str
    raw_accounting_units: int
    natural_units: int
    normal_side: PostingSide
    balance_semantics: str
    outstanding_units: int | None
    overpayment_units: int | None


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
    return _get_book_balances(
        session,
        book_id,
        as_of_book_position=as_of_book_position,
        verify_current_projection=False,
    )


def get_verified_book_balances(
    session: Session,
    book_id: UUID,
) -> BalanceSnapshot:
    """Compare the current projection to immutable postings and safely fall back."""

    return _get_book_balances(
        session,
        book_id,
        as_of_book_position=None,
        verify_current_projection=True,
    )


def _get_book_balances(
    session: Session,
    book_id: UUID,
    *,
    as_of_book_position: int | None,
    verify_current_projection: bool,
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
    use_current_projection = as_of_book_position is None
    as_of = head if as_of_book_position is None else as_of_book_position
    if type(as_of) is not int or not 0 <= as_of <= head:
        raise ValueError("as_of_book_position is outside the Book head")

    parity: bool | None = None
    if use_current_projection:
        projection = {
            (row.account_id, row.asset_code): int(row.balance_units)
            for row in session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == book_id
                )
            )
        }
        values = projection
        if verify_current_projection:
            reference = _load_reference_balances(session, book_id, as_of)
            parity = projection == reference
            if not parity:
                values = reference
    else:
        values = _load_reference_balances(session, book_id, as_of)
    account_semantics = {
        (account_id, asset_code): (
            AccountType(account_type),
            account_subtype,
            account_status,
        )
        for (
            account_id,
            asset_code,
            account_type,
            account_subtype,
            account_status,
        ) in session.execute(
            select(
                AccountRecord.account_id,
                AccountRecord.asset_code,
                AccountRecord.account_type,
                AccountRecord.account_subtype,
                AccountRecord.status,
            ).where(AccountRecord.book_id == book_id)
        )
    }
    missing_accounts = set(values).difference(account_semantics)
    if missing_accounts:
        raise RuntimeError("balance projection references an unavailable account")

    items = tuple(
        build_balance_item(
            account_id=account_id,
            asset_code=asset_code,
            account_type=account_semantics[(account_id, asset_code)][0],
            account_subtype=account_semantics[(account_id, asset_code)][1],
            account_status=account_semantics[(account_id, asset_code)][2],
            raw_accounting_units=raw_accounting_units,
        )
        for (account_id, asset_code), raw_accounting_units in sorted(
            values.items(), key=lambda item: (str(item[0][0]), item[0][1])
        )
    )
    return BalanceSnapshot(
        items=items,
        as_of_book_position=as_of,
        projection_matches_reference=parity,
    )


def _load_reference_balances(
    session: Session,
    book_id: UUID,
    as_of_book_position: int,
) -> dict[tuple[UUID, str], int]:
    signed_units = case(
        (JournalPostingRecord.side == "debit", JournalPostingRecord.units),
        else_=-JournalPostingRecord.units,
    )
    rows = session.execute(
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
            JournalTransactionRecord.source_position <= as_of_book_position,
        )
        .group_by(JournalPostingRecord.account_id, JournalPostingRecord.asset_code)
    )
    return {
        (account_id, asset_code): int(units) for account_id, asset_code, units in rows
    }


def build_balance_item(
    *,
    account_id: UUID,
    asset_code: str,
    account_type: AccountType,
    account_subtype: str | None = None,
    account_status: str,
    raw_accounting_units: int,
) -> BalanceItem:
    credit_normal = account_type in {
        AccountType.LIABILITY,
        AccountType.EQUITY,
        AccountType.INCOME,
    }
    normal_side = PostingSide.CREDIT if credit_normal else PostingSide.DEBIT
    natural_units = -raw_accounting_units if credit_normal else raw_accounting_units
    is_liability = account_type is AccountType.LIABILITY
    return BalanceItem(
        account_id=account_id,
        asset_code=asset_code,
        account_type=account_type,
        account_subtype=account_subtype,
        account_status=account_status,
        raw_accounting_units=raw_accounting_units,
        natural_units=natural_units,
        normal_side=normal_side,
        balance_semantics=f"natural_{account_type.value}_balance",
        outstanding_units=max(natural_units, 0) if is_liability else None,
        overpayment_units=max(-natural_units, 0) if is_liability else None,
    )


__all__ = [
    "BalanceItem",
    "BalanceSnapshot",
    "build_balance_item",
    "get_book_balances",
    "get_verified_book_balances",
]
