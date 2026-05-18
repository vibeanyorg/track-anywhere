from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .books import DEFAULT_OWNER_ID
from .category_models import Category, CategoryVersion, ClassificationEvent


class CategoryHistoryMixin:
    def active_version(self, category_id: str) -> CategoryVersion:
        versions = [item for item in self.versions.values() if item.category_id == category_id and item.valid_to is None]
        if versions:
            return sorted(versions, key=lambda item: item.valid_from)[-1]
        return self._record_version(self.get(category_id), "snapshot")

    def path_snapshot(self, category_id: str) -> dict[str, str | None]:
        category = self.get(category_id)
        version = self.active_version(category_id)
        return {
            "category_id": category.category_id,
            "category_version_id": version.category_version_id,
            "kind": category.kind,
            "primary": category.primary,
            "secondary": category.secondary,
            "path": category.path_cache,
        }

    def _record_version(self, category: Category, reason: str) -> CategoryVersion:
        for version in self.versions.values():
            if version.category_id == category.category_id and version.valid_to is None:
                version.valid_to = datetime.now(timezone.utc)
        version = CategoryVersion(
            category_version_id=f"catv_{uuid4().hex}",
            category_id=category.category_id,
            book_id=category.book_id,
            name=category.name,
            parent_id=category.parent_id,
            path=category.path_cache,
            icon=category.icon,
            color=category.color,
            change_reason=reason,
        )
        self.versions[version.category_version_id] = version
        return version

    def _record_event(
        self,
        event_type: str,
        book_id: str,
        source_id: str | None,
        *,
        target_id: str | None = None,
        affected_line_count: int = 0,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        actor_id: str = DEFAULT_OWNER_ID,
    ) -> ClassificationEvent:
        event = ClassificationEvent(
            classification_event_id=f"cevt_{uuid4().hex}",
            book_id=book_id,
            event_type=event_type,
            source_category_id=source_id,
            target_category_id=target_id,
            affected_line_count=affected_line_count,
            before=before or {},
            after=after or {},
            created_by=actor_id,
        )
        self.events[event.classification_event_id] = event
        return event

    @staticmethod
    def _snapshot(category: Category) -> dict[str, object]:
        return {
            "category_id": category.category_id,
            "kind": category.kind,
            "primary": category.primary,
            "secondary": category.secondary,
            "parent_id": category.parent_id,
            "name": category.name,
            "path": category.path_cache,
            "status": category.status,
            "version": category.version,
        }
