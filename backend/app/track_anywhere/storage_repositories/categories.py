from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from ..books import DEFAULT_BOOK_ID
from ..categories import Category
from ..category_models import normalize_key
from ..domain_storage_models import CategoryAliasRecord, CategoryVersionRecord, ClassificationEventRecord
from ..errors import NotFound, ValidationError
from ..storage_json import to_jsonable
from ..storage_models import CategoryRecord
from ..storage_upsert_writers import upsert_record


class CategoryRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def list_categories(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
        status: str | None = "active",
    ) -> list[Category]:
        statement = select(CategoryRecord)
        if book_id is not None:
            statement = statement.where(CategoryRecord.book_id == book_id)
        if status is not None:
            statement = statement.where(CategoryRecord.status == status)
        if kind is not None:
            statement = statement.where(CategoryRecord.kind == kind)
        if parent_id is not None:
            statement = statement.where(CategoryRecord.parent_id == parent_id)
        if name is not None:
            statement = statement.where(CategoryRecord.normalized_name == normalize_key(name))
        categories = [category_from_record(row) for row in self.session.scalars(statement)]
        return sorted(
            categories,
            key=lambda category: (
                category.book_id,
                category.kind,
                category.primary,
                category.level,
                category.secondary or "",
                category.sort_order,
                category.category_id,
            ),
        )

    def get_category(self, category_id: str) -> Category:
        row = self.session.get(CategoryRecord, category_id)
        if row is None:
            raise NotFound(f"category not found: {category_id}")
        return category_from_record(row)

    def find_category_by_path(self, *, book_id: str, kind: str, path: str) -> Category | None:
        if kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")
        parts = [_clean_path_part(part) for part in path.split("/")]
        parts = [part for part in parts if part]
        if not parts:
            raise ValidationError("category path must not be blank")
        if len(parts) > 2:
            raise ValidationError("category path supports at most two levels")
        parent_matches = self.list_categories(kind=kind, name=parts[0], parent_id=None, book_id=book_id)
        parent = parent_matches[0] if parent_matches else None
        if len(parts) == 1 or parent is None:
            return parent
        child_matches = self.list_categories(kind=kind, name=parts[1], parent_id=parent.category_id, book_id=book_id)
        return child_matches[0] if child_matches else None

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


def category_from_record(row: CategoryRecord) -> Category:
    primary, secondary = _category_names_from_path(row)
    return Category(
        category_id=row.category_id,
        book_id=row.book_id,
        kind=row.kind,
        primary=primary,
        secondary=secondary,
        parent_id=row.parent_id,
        name=row.name,
        normalized_name=row.normalized_name,
        level=row.level,
        path_cache=row.path_cache,
        icon=row.icon,
        color=row.color,
        sort_order=row.sort_order,
        status=row.status,
        version=row.version,
    )


def _category_names_from_path(row: CategoryRecord) -> tuple[str, str | None]:
    parts = [_clean_path_part(part) for part in (row.path_cache or "").split("/")]
    parts = [part for part in parts if part]
    if row.level == 2 and len(parts) >= 2:
        return parts[0], parts[-1]
    return (parts[0] if parts else row.name), None


def _clean_path_part(value: str) -> str:
    return " ".join(value.strip().split())
