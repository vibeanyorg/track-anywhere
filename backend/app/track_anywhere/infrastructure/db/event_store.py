from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ...application.event_batch import (
    AppendBatchResult,
    PendingEvent,
    StreamKey,
)
from ...serialization.canonical_json import EventHashEnvelope, event_hash
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from .models.event_store import (
    BookEventHeadRecord,
    EventStreamHeadRecord,
    LedgerEventRecord,
)


_HASH_PLACEHOLDER_RECORDED_AT = datetime(1970, 1, 1, tzinfo=UTC)


class AppendBatchValidationError(ValueError):
    pass


class BookEventHeadNotFound(LookupError):
    pass


class StreamVersionConflict(RuntimeError):
    def __init__(
        self,
        stream_key: StreamKey,
        *,
        expected_version: int,
        actual_version: int,
    ) -> None:
        self.stream_key = stream_key
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            "event stream version conflict "
            f"(expected {expected_version}, actual {actual_version})"
        )


class PostgresEventStore:
    """Low-level append primitive; LedgerCommitter is the future public writer."""

    def _append_batch(
        self,
        session: Session,
        *,
        book_id: UUID,
        expected_stream_versions: Mapping[StreamKey, int],
        events: Sequence[PendingEvent],
    ) -> AppendBatchResult:
        pending_events = self._validate_batch(
            book_id=book_id,
            expected_stream_versions=expected_stream_versions,
            events=events,
        )
        head = session.execute(
            select(BookEventHeadRecord)
            .where(BookEventHeadRecord.book_id == book_id)
            .with_for_update()
        ).scalar_one_or_none()
        if head is None:
            raise BookEventHeadNotFound("book event head not found")

        stream_keys = tuple(dict.fromkeys(event.stream_key for event in pending_events))
        stream_heads = tuple(
            session.scalars(
                select(EventStreamHeadRecord)
                .where(
                    EventStreamHeadRecord.book_id == book_id,
                    tuple_(
                        EventStreamHeadRecord.stream_type,
                        EventStreamHeadRecord.stream_id,
                    ).in_(stream_keys),
                )
                .with_for_update()
            )
        )
        heads_by_key = {
            (stream_head.stream_type, stream_head.stream_id): stream_head
            for stream_head in stream_heads
        }
        current_versions = {
            key: heads_by_key[key].last_version if key in heads_by_key else 0
            for key in stream_keys
        }
        for key in stream_keys:
            expected_version = expected_stream_versions[key]
            actual_version = current_versions[key]
            if expected_version != actual_version:
                raise StreamVersionConflict(
                    key,
                    expected_version=expected_version,
                    actual_version=actual_version,
                )

        first_position = head.last_position + 1
        next_position = head.last_position
        previous_hash = head.last_hash
        next_versions = dict(current_versions)
        stored_events: list[LedgerEventRecord] = []
        terminal_by_stream: dict[StreamKey, LedgerEventRecord] = {}

        for pending in pending_events:
            next_position += 1
            stream_key = pending.stream_key
            next_versions[stream_key] += 1
            stored_payload = PRODUCTION_EVENT_REGISTRY.dump_registered(pending.payload)
            event_type = type(pending.payload).event_type
            schema_version = type(pending.payload).schema_version
            hashed = event_hash(
                EventHashEnvelope(
                    event_id=pending.event_id,
                    book_id=book_id,
                    book_position=next_position,
                    # These two database-generated diagnostic fields are deliberately
                    # excluded from the frozen hash envelope, but the validator still
                    # requires legal placeholder values.
                    global_sequence=1,
                    recorded_at=_HASH_PLACEHOLDER_RECORDED_AT,
                    stream_type=pending.stream_type,
                    stream_id=pending.stream_id,
                    stream_version=next_versions[stream_key],
                    event_type=event_type,
                    event_schema_version=schema_version,
                    command_id=pending.command_id,
                    actor_subject_id=pending.actor_subject_id,
                    correlation_id=pending.correlation_id,
                    causation_event_id=pending.causation_event_id,
                    effective_at=pending.effective_at,
                    previous_hash=previous_hash,
                ),
                stored_payload,
            )
            stored = LedgerEventRecord(
                event_id=pending.event_id,
                book_id=book_id,
                book_position=next_position,
                stream_type=pending.stream_type,
                stream_id=pending.stream_id,
                stream_version=next_versions[stream_key],
                event_type=event_type,
                event_schema_version=schema_version,
                command_id=pending.command_id,
                actor_subject_id=pending.actor_subject_id,
                correlation_id=pending.correlation_id,
                causation_event_id=pending.causation_event_id,
                effective_at=pending.effective_at,
                payload=stored_payload,
                previous_hash=previous_hash,
                event_hash=hashed,
            )
            stored_events.append(stored)
            terminal_by_stream[stream_key] = stored
            previous_hash = hashed

        session.add_all(stored_events)
        # Heads have exact terminal-event FKs and integrity triggers, so the immutable
        # event rows must exist before either Book or stream heads advance.
        session.flush(stored_events)

        for stream_key, terminal in terminal_by_stream.items():
            stream_head = heads_by_key.get(stream_key)
            if stream_head is None:
                stream_head = EventStreamHeadRecord(
                    book_id=book_id,
                    stream_type=stream_key[0],
                    stream_id=stream_key[1],
                    last_version=terminal.stream_version,
                    last_book_position=terminal.book_position,
                    last_event_id=terminal.event_id,
                )
                session.add(stream_head)
            else:
                stream_head.last_version = terminal.stream_version
                stream_head.last_book_position = terminal.book_position
                stream_head.last_event_id = terminal.event_id

        head.last_position = next_position
        head.last_hash = previous_hash
        session.flush()
        return AppendBatchResult(
            positions=range(first_position, next_position + 1),
            terminal_hash=previous_hash,
            event_ids=tuple(event.event_id for event in pending_events),
        )

    @staticmethod
    def _validate_batch(
        *,
        book_id: UUID,
        expected_stream_versions: Mapping[StreamKey, int],
        events: Sequence[PendingEvent],
    ) -> tuple[PendingEvent, ...]:
        if type(book_id) is not UUID:
            raise AppendBatchValidationError("book_id must be a UUID")
        if not isinstance(expected_stream_versions, Mapping):
            raise AppendBatchValidationError(
                "expected stream versions must be a mapping"
            )
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise AppendBatchValidationError("events must be a sequence")
        pending_events = tuple(events)
        if not pending_events:
            raise AppendBatchValidationError("event batch must not be empty")
        if any(type(event) is not PendingEvent for event in pending_events):
            raise AppendBatchValidationError("event batch contains an invalid event")

        expected = dict(expected_stream_versions)
        for key, version in expected.items():
            if (
                type(key) is not tuple
                or len(key) != 2
                or type(key[0]) is not str
                or type(key[1]) is not UUID
                or type(version) is not int
                or version < 0
            ):
                raise AppendBatchValidationError("invalid expected stream version")
        touched = {(event.stream_type, event.stream_id) for event in pending_events}
        if set(expected) != touched:
            raise AppendBatchValidationError(
                "expected stream versions must exactly cover touched streams"
            )

        for event in pending_events:
            if type(event.event_id) is not UUID:
                raise AppendBatchValidationError("event_id must be a UUID")
            if type(event.stream_type) is not str or type(event.stream_id) is not UUID:
                raise AppendBatchValidationError("event stream identity is invalid")
            if type(event.command_id) is not UUID:
                raise AppendBatchValidationError("command_id must be a UUID")
            if type(event.actor_subject_id) is not str:
                raise AppendBatchValidationError("actor subject is invalid")
            if type(event.correlation_id) is not UUID:
                raise AppendBatchValidationError("correlation_id must be a UUID")
            if (
                event.causation_event_id is not None
                and type(event.causation_event_id) is not UUID
            ):
                raise AppendBatchValidationError("causation_event_id must be a UUID")
        return pending_events


__all__ = [
    "AppendBatchValidationError",
    "BookEventHeadNotFound",
    "PostgresEventStore",
    "StreamVersionConflict",
]
