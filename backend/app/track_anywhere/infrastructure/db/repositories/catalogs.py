from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from . import RowLock, apply_row_lock


class CatalogNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    asset_code: str
    kind: str
    ledger_scale: int
    input_scale: int
    display_scale: int
    current_name: str
    status: str


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    book_id: UUID
    current_name: str
    base_asset_code: str | None
    write_state: str


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    book_id: UUID
    account_id: UUID
    asset_code: str
    account_type: str
    account_subtype: str | None
    system_role: str | None
    current_name: str
    status: str


@dataclass(frozen=True, slots=True)
class CategorySnapshot:
    book_id: UUID
    category_id: UUID
    parent_category_id: UUID | None
    current_name: str
    current_version_id: UUID | None
    status: str


@dataclass(frozen=True, slots=True)
class CategoryVersionSnapshot:
    book_id: UUID
    category_id: UUID
    category_version_id: UUID
    parent_category_id: UUID | None
    name: str
    status: str
    change_reason_code: str


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_asset(
        self,
        asset_code: str,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> AssetSnapshot:
        record = self._one(
            apply_row_lock(
                select(AssetRecord)
                .where(AssetRecord.asset_code == asset_code)
                .execution_options(populate_existing=True),
                lock,
            ),
            "asset",
        )
        return AssetSnapshot(
            asset_code=record.asset_code,
            kind=record.kind,
            ledger_scale=record.ledger_scale,
            input_scale=record.input_scale,
            display_scale=record.display_scale,
            current_name=record.current_name,
            status=record.status,
        )

    def get_book(
        self,
        book_id: UUID,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> BookSnapshot:
        record = self._one(
            apply_row_lock(
                select(BookRecord).where(BookRecord.book_id == book_id),
                lock,
            ),
            "book",
        )
        return BookSnapshot(
            book_id=record.book_id,
            current_name=record.current_name,
            base_asset_code=record.base_asset_code,
            write_state=record.write_state,
        )

    def get_account(
        self,
        book_id: UUID,
        account_id: UUID,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> AccountSnapshot:
        record = self._one(
            apply_row_lock(
                select(AccountRecord)
                .where(
                    AccountRecord.book_id == book_id,
                    AccountRecord.account_id == account_id,
                )
                .execution_options(populate_existing=True),
                lock,
            ),
            "account",
        )
        return AccountSnapshot(
            book_id=record.book_id,
            account_id=record.account_id,
            asset_code=record.asset_code,
            account_type=record.account_type,
            account_subtype=record.account_subtype,
            system_role=record.system_role,
            current_name=record.current_name,
            status=record.status,
        )

    def get_category(
        self,
        book_id: UUID,
        category_id: UUID,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> CategorySnapshot:
        record = self._one(
            apply_row_lock(
                select(CategoryRecord).where(
                    CategoryRecord.book_id == book_id,
                    CategoryRecord.category_id == category_id,
                ),
                lock,
            ),
            "category",
        )
        return CategorySnapshot(
            book_id=record.book_id,
            category_id=record.category_id,
            parent_category_id=record.parent_category_id,
            current_name=record.current_name,
            current_version_id=record.current_version_id,
            status=record.status,
        )

    def get_category_version(
        self,
        book_id: UUID,
        category_id: UUID,
        category_version_id: UUID,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> CategoryVersionSnapshot:
        record = self._one(
            apply_row_lock(
                select(CategoryVersionRecord).where(
                    CategoryVersionRecord.book_id == book_id,
                    CategoryVersionRecord.category_id == category_id,
                    CategoryVersionRecord.category_version_id == category_version_id,
                ),
                lock,
            ),
            "category version",
        )
        return CategoryVersionSnapshot(
            book_id=record.book_id,
            category_id=record.category_id,
            category_version_id=record.category_version_id,
            parent_category_id=record.parent_category_id,
            name=record.name,
            status=record.status,
            change_reason_code=record.change_reason_code,
        )

    def _one(self, statement, entity_name: str):
        record = self._session.execute(statement).scalar_one_or_none()
        if record is None:
            raise CatalogNotFound(f"{entity_name} not found in requested scope")
        return record


__all__ = [
    "AccountSnapshot",
    "AssetSnapshot",
    "BookSnapshot",
    "CatalogNotFound",
    "CatalogRepository",
    "CategorySnapshot",
    "CategoryVersionSnapshot",
]
