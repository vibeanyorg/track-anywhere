from __future__ import annotations

from sqlalchemy.orm import Session

from .domain_storage_models import (
    CategoryAliasRecord,
    CategoryVersionRecord,
    ClassificationEventRecord,
)
from .storage_json import to_jsonable


class DomainStorageWriters:
    def _save_category_history(
        self,
        session: Session,
        *,
        aliases,
        versions,
        events,
    ) -> None:
        for alias in aliases:
            self._upsert_record(
                session,
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
            self._upsert_record(
                session,
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
            self._upsert_record(
                session,
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
