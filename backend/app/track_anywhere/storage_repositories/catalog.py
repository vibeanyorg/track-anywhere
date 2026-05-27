from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from ..books import DEFAULT_BOOK_ID
from ..counterparties import Counterparty, normalize_counterparty_name, normalize_counterparty_slug
from ..counterparty_storage_models import CounterpartyRecord
from ..domain_storage_models import (
    BookMemberRecord,
    CategoryAliasRecord,
    CategoryVersionRecord,
    ClassificationEventRecord,
    LedgerBookRecord,
)
from ..errors import NotFound
from ..storage_json import to_jsonable
from ..storage_models import AssetRecord, CategoryRecord
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


class CategoryRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, categories: Iterable[Any]) -> None:
        for category in categories:
            upsert_record(
                self.session,
                CategoryRecord,
                {
                    "category_id": category.category_id,
                    "book_id": category.book_id,
                    "kind": category.kind,
                    "parent_id": category.parent_id,
                    "name": category.name,
                    "normalized_name": category.normalized_name,
                    "level": category.level,
                    "path_cache": category.path_cache,
                    "icon": category.icon,
                    "color": category.color,
                    "sort_order": category.sort_order,
                    "status": category.status,
                    "version": category.version,
                },
                ["category_id"],
            )

    def save_history(self, *, aliases, versions, events) -> None:
        for alias in aliases:
            upsert_record(
                self.session,
                CategoryAliasRecord,
                {
                    "alias_id": alias.alias_id,
                    "book_id": alias.book_id,
                    "category_id": alias.category_id,
                    "alias": alias.alias,
                    "normalized_alias": alias.normalized_alias,
                    "locale": alias.locale,
                    "source": alias.source,
                    "confidence": alias.confidence,
                    "status": alias.status,
                    "version": alias.version,
                },
                ["alias_id"],
            )
        for version in versions:
            upsert_record(
                self.session,
                CategoryVersionRecord,
                {
                    "category_version_id": version.category_version_id,
                    "category_id": version.category_id,
                    "book_id": version.book_id,
                    "name": version.name,
                    "parent_id": version.parent_id,
                    "path": version.path,
                    "icon": version.icon,
                    "color": version.color,
                    "valid_from": version.valid_from.isoformat(),
                    "valid_to": version.valid_to.isoformat() if version.valid_to else None,
                    "change_reason": version.change_reason,
                    "version": version.version,
                },
                ["category_version_id"],
            )
        for event in events:
            upsert_record(
                self.session,
                ClassificationEventRecord,
                {
                    "classification_event_id": event.classification_event_id,
                    "book_id": event.book_id,
                    "event_type": event.event_type,
                    "source_category_id": event.source_category_id,
                    "target_category_id": event.target_category_id,
                    "affected_line_count": event.affected_line_count,
                    "before": to_jsonable(event.before),
                    "after": to_jsonable(event.after),
                    "rollback": to_jsonable(event.rollback),
                    "created_by": event.created_by,
                    "created_at": event.created_at.isoformat(),
                    "version": event.version,
                },
                ["classification_event_id"],
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
