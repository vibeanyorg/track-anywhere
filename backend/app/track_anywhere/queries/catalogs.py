from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.journal import AccountType
from ..infrastructure.db.models.auth import BookMemberRecord
from ..infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
    CategoryRecord,
)
from ..infrastructure.db.models.projections import AccountBalanceRecord
from .balances import BalanceItem, build_balance_item


@dataclass(frozen=True, slots=True)
class BookSummary:
    book_id: UUID
    current_name: str
    base_asset_code: str | None
    write_state: str


@dataclass(frozen=True, slots=True)
class AssetSummary:
    asset_code: str
    kind: str
    ledger_scale: int
    input_scale: int
    display_scale: int
    current_name: str
    status: str


@dataclass(frozen=True, slots=True)
class AccountSummary:
    account_id: UUID
    asset_code: str
    account_type: AccountType
    account_subtype: str | None
    system_role: str | None
    current_name: str
    status: str
    balance: BalanceItem


@dataclass(frozen=True, slots=True)
class CategorySummary:
    category_id: UUID
    parent_category_id: UUID | None
    current_version_id: UUID | None
    current_name: str
    status: str


def list_accessible_books(
    session: Session,
    *,
    user_id: str,
    restricted_book_id: UUID | None = None,
) -> tuple[BookSummary, ...]:
    if type(user_id) is not str or not user_id.strip():
        raise ValueError("user_id must be nonblank")
    statement = (
        select(BookRecord)
        .join(
            BookMemberRecord,
            BookMemberRecord.book_id == BookRecord.book_id,
        )
        .where(
            BookMemberRecord.user_id == user_id,
            BookMemberRecord.status == "active",
            BookMemberRecord.revoked_at.is_(None),
            BookMemberRecord.scopes.contains(["ledger:read"]),
        )
        .order_by(BookRecord.current_name, BookRecord.book_id)
    )
    if restricted_book_id is not None:
        statement = statement.where(BookRecord.book_id == restricted_book_id)
    return tuple(
        BookSummary(
            book_id=row.book_id,
            current_name=row.current_name,
            base_asset_code=row.base_asset_code,
            write_state=row.write_state,
        )
        for row in session.scalars(statement)
    )


def list_assets(session: Session, book_id: UUID) -> tuple[AssetSummary, ...]:
    _require_book(session, book_id)
    return tuple(
        AssetSummary(
            asset_code=row.asset_code,
            kind=row.kind,
            ledger_scale=row.ledger_scale,
            input_scale=row.input_scale,
            display_scale=row.display_scale,
            current_name=row.current_name,
            status=row.status,
        )
        for row in session.scalars(
            select(AssetRecord).order_by(AssetRecord.asset_code)
        )
    )


def list_accounts(
    session: Session,
    book_id: UUID,
    *,
    account_type: str | None = None,
    account_subtype: str | None = None,
    status: str | None = None,
    asset_code: str | None = None,
    name: str | None = None,
) -> tuple[AccountSummary, ...]:
    _require_book(session, book_id)
    if account_type is not None:
        try:
            AccountType(account_type)
        except ValueError:
            raise ValueError("account_type is invalid") from None
    if status is not None and status not in {"active", "closed"}:
        raise ValueError("status must be active or closed")
    statement = (
        select(AccountRecord, AccountBalanceRecord.balance_units)
        .outerjoin(
            AccountBalanceRecord,
            (AccountBalanceRecord.book_id == AccountRecord.book_id)
            & (AccountBalanceRecord.account_id == AccountRecord.account_id)
            & (AccountBalanceRecord.asset_code == AccountRecord.asset_code),
        )
        .where(AccountRecord.book_id == book_id)
        .order_by(AccountRecord.current_name, AccountRecord.account_id)
    )
    if account_type is not None:
        statement = statement.where(AccountRecord.account_type == account_type)
    if account_subtype is not None:
        statement = statement.where(AccountRecord.account_subtype == account_subtype)
    if status is not None:
        statement = statement.where(AccountRecord.status == status)
    if asset_code is not None:
        statement = statement.where(AccountRecord.asset_code == asset_code)
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("name must be nonblank")
        escaped = (
            normalized_name.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        statement = statement.where(
            AccountRecord.current_name.ilike(f"%{escaped}%", escape="\\")
        )
    return tuple(
        _account_summary(record, balance_units)
        for record, balance_units in session.execute(statement)
    )


def get_account(
    session: Session,
    book_id: UUID,
    account_id: UUID,
) -> AccountSummary:
    _require_book(session, book_id)
    row = session.execute(
        select(AccountRecord, AccountBalanceRecord.balance_units)
        .outerjoin(
            AccountBalanceRecord,
            (AccountBalanceRecord.book_id == AccountRecord.book_id)
            & (AccountBalanceRecord.account_id == AccountRecord.account_id)
            & (AccountBalanceRecord.asset_code == AccountRecord.asset_code),
        )
        .where(
            AccountRecord.book_id == book_id,
            AccountRecord.account_id == account_id,
        )
    ).one_or_none()
    if row is None:
        raise LookupError("Account not found")
    return _account_summary(row[0], row[1])


def list_categories(
    session: Session,
    book_id: UUID,
) -> tuple[CategorySummary, ...]:
    _require_book(session, book_id)
    return tuple(
        CategorySummary(
            category_id=row.category_id,
            parent_category_id=row.parent_category_id,
            current_version_id=row.current_version_id,
            current_name=row.current_name,
            status=row.status,
        )
        for row in session.scalars(
            select(CategoryRecord)
            .where(CategoryRecord.book_id == book_id)
            .order_by(CategoryRecord.current_name, CategoryRecord.category_id)
        )
    )


def _account_summary(
    record: AccountRecord,
    balance_units: object,
) -> AccountSummary:
    account_type = AccountType(record.account_type)
    balance = build_balance_item(
        account_id=record.account_id,
        asset_code=record.asset_code,
        account_type=account_type,
        account_subtype=record.account_subtype,
        account_status=record.status,
        raw_accounting_units=0 if balance_units is None else int(balance_units),
    )
    return AccountSummary(
        account_id=record.account_id,
        asset_code=record.asset_code,
        account_type=account_type,
        account_subtype=record.account_subtype,
        system_role=record.system_role,
        current_name=record.current_name,
        status=record.status,
        balance=balance,
    )


def _require_book(session: Session, book_id: UUID) -> None:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    if session.get(BookRecord, book_id) is None:
        raise LookupError("Book not found")


__all__ = [
    "AccountSummary",
    "AssetSummary",
    "BookSummary",
    "CategorySummary",
    "get_account",
    "list_accessible_books",
    "list_accounts",
    "list_assets",
    "list_categories",
]
