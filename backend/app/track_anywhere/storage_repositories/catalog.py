from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from ..books import DEFAULT_BOOK_ID
from ..counterparties import Counterparty, normalize_counterparty_name, normalize_counterparty_slug
from ..counterparty_storage_models import CounterpartyRecord
from ..domain_storage_models import BookMemberRecord, LedgerBookRecord
from ..errors import NotFound
from ..storage_json import to_jsonable
from ..storage_models import AssetRecord
from ..storage_upsert_writers import upsert_record


class AssetRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, assets: Iterable[Any]) -> None:
        for asset in assets:
            upsert_record(
                self.session,
                AssetRecord,
                {
                    "asset_code": asset.asset_code,
                    "kind": asset.kind,
                    "scale": asset.scale,
                    "display_scale": asset.display_scale if asset.display_scale is not None else asset.scale,
                    "name": asset.name,
                    "status": asset.status,
                    "version": asset.version,
                },
                ["asset_code"],
            )


class BookRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, books: Iterable[Any], members: Iterable[Any]) -> None:
        for book in books:
            self.session.merge(
                LedgerBookRecord(
                    book_id=book.book_id,
                    name=book.name,
                    kind=book.kind,
                    base_currency=book.base_currency,
                    timezone=book.timezone,
                    status=book.status,
                    template_key=book.template_key,
                    settings=to_jsonable(book.settings),
                    created_by=book.created_by,
                    version=book.version,
                )
            )
        for member in members:
            self.session.merge(
                BookMemberRecord(
                    book_id=member.book_id,
                    user_id=member.user_id,
                    role=member.role,
                    status=member.status,
                    scopes=list(member.scopes),
                    version=member.version,
                )
            )


class CounterpartyRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def list_counterparties(
        self,
        *,
        book_id: str | None = DEFAULT_BOOK_ID,
        kind: str | None = None,
        status: str | None = "active",
        name: str | None = None,
    ) -> list[Counterparty]:
        statement = select(CounterpartyRecord)
        if book_id is not None:
            statement = statement.where(CounterpartyRecord.book_id == book_id)
        if kind is not None:
            statement = statement.where(CounterpartyRecord.kind == kind)
        if status is not None:
            statement = statement.where(CounterpartyRecord.status == status)
        counterparties = [counterparty_from_record(row) for row in self.session.scalars(statement)]
        if name is not None:
            needle = normalize_counterparty_name(name).casefold()
            counterparties = [item for item in counterparties if needle in item.name.casefold()]
        return sorted(counterparties, key=lambda item: (item.name.casefold(), item.counterparty_id))

    def get_counterparty(
        self,
        counterparty_id: str,
        *,
        status: str | None = "active",
    ) -> Counterparty:
        row = self.session.get(CounterpartyRecord, counterparty_id)
        if row is None or (status is not None and row.status != status):
            raise NotFound(f"counterparty not found: {counterparty_id}")
        return counterparty_from_record(row)

    def get_counterparty_by_slug(
        self,
        *,
        book_id: str,
        slug: str,
        status: str | None = "active",
    ) -> Counterparty:
        slug = normalize_counterparty_slug(slug)
        statement = select(CounterpartyRecord).where(
            CounterpartyRecord.book_id == book_id,
            CounterpartyRecord.slug == slug,
        )
        if status is not None:
            statement = statement.where(CounterpartyRecord.status == status)
        row = self.session.scalars(statement).first()
        if row is None:
            raise NotFound(f"counterparty slug not found in book: {book_id}/{slug}")
        return counterparty_from_record(row)

    def get_counterparty_by_name(
        self,
        *,
        book_id: str,
        name: str,
        status: str | None = "active",
    ) -> Counterparty:
        normalized = normalize_counterparty_name(name).casefold()
        for counterparty in self.list_counterparties(book_id=book_id, status=status):
            if counterparty.name.casefold() == normalized:
                return counterparty
        raise NotFound(f"counterparty name not found in book: {book_id}/{normalized}")

    def save(self, counterparties: Iterable[Any]) -> None:
        for counterparty in counterparties:
            self.session.merge(
                CounterpartyRecord(
                    counterparty_id=counterparty.counterparty_id,
                    book_id=counterparty.book_id,
                    slug=counterparty.slug,
                    name=counterparty.name,
                    kind=counterparty.kind,
                    status=counterparty.status,
                    version=counterparty.version,
                )
            )


def counterparty_from_record(row: CounterpartyRecord) -> Counterparty:
    return Counterparty(
        counterparty_id=row.counterparty_id,
        book_id=row.book_id,
        slug=row.slug,
        name=row.name,
        kind=row.kind,
        status=row.status,
        version=row.version,
    )
