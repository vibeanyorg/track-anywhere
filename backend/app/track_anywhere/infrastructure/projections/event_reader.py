from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...serialization.canonical_json import JSONValue
from ..db.models.catalog import BookRecord
from ..db.models.event_store import LedgerEventRecord


class EventReaderValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredEventSnapshot:
    event_id: UUID
    book_id: UUID
    book_position: int
    stream_type: str
    stream_id: UUID
    stream_version: int
    event_type: str
    event_schema_version: int
    command_id: UUID
    actor_subject_id: str
    correlation_id: UUID
    causation_event_id: UUID | None
    effective_at: datetime
    recorded_at: datetime
    payload: dict[str, JSONValue]
    previous_hash: bytes
    event_hash: bytes


class PerBookEventReader:
    """Fetch committed source events using only a Book-local checkpoint."""

    def discover_book_ids(self, session: Session) -> tuple[UUID, ...]:
        return tuple(
            session.scalars(select(BookRecord.book_id).order_by(BookRecord.book_id))
        )

    def read_after(
        self,
        session: Session,
        *,
        book_id: UUID,
        after_book_position: int,
        limit: int,
    ) -> tuple[StoredEventSnapshot, ...]:
        if type(book_id) is not UUID:
            raise EventReaderValidationError("book_id must be a UUID")
        if type(after_book_position) is not int or after_book_position < 0:
            raise EventReaderValidationError(
                "after_book_position must be a nonnegative integer"
            )
        if type(limit) is not int or limit < 1 or limit > 10_000:
            raise EventReaderValidationError("limit is outside its allowed range")

        records = session.scalars(
            select(LedgerEventRecord)
            .where(
                LedgerEventRecord.book_id == book_id,
                LedgerEventRecord.book_position > after_book_position,
            )
            .order_by(LedgerEventRecord.book_position)
            .limit(limit)
        )
        return tuple(self._snapshot(record) for record in records)

    @staticmethod
    def _snapshot(record: LedgerEventRecord) -> StoredEventSnapshot:
        return StoredEventSnapshot(
            event_id=record.event_id,
            book_id=record.book_id,
            book_position=record.book_position,
            stream_type=record.stream_type,
            stream_id=record.stream_id,
            stream_version=record.stream_version,
            event_type=record.event_type,
            event_schema_version=record.event_schema_version,
            command_id=record.command_id,
            actor_subject_id=record.actor_subject_id,
            correlation_id=record.correlation_id,
            causation_event_id=record.causation_event_id,
            effective_at=record.effective_at,
            recorded_at=record.recorded_at,
            payload=deepcopy(record.payload),
            previous_hash=record.previous_hash,
            event_hash=record.event_hash,
        )


__all__ = [
    "EventReaderValidationError",
    "PerBookEventReader",
    "StoredEventSnapshot",
]
