from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .event_batch import AppendBatchResult, PendingEvent, StreamKey
from .idempotency import CommandResult, IdempotencyValidationError
from ..infrastructure.db.event_store import (
    BookEventHeadNotFound,
    PostgresEventStore,
)
from ..infrastructure.db.models.catalog import BookRecord
from ..infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from ..infrastructure.projections.synchronous import SynchronousProjector
from ..serialization.canonical_json import JSONValue


class BookWritePaused(PermissionError):
    pass


class LedgerWriteBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LockedBookHead:
    book_id: UUID
    last_position: int
    last_hash: bytes
    _lock_token: UUID = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class LedgerWritePlan:
    expected_stream_versions: Mapping[StreamKey, int]
    events: tuple[PendingEvent, ...]
    response_schema_version: int
    status_code: int
    body: dict[str, JSONValue] | list[JSONValue]

    def __post_init__(self) -> None:
        expected = dict(self.expected_stream_versions)
        events = tuple(self.events)
        if not events:
            raise IdempotencyValidationError("ledger write plan must contain events")
        object.__setattr__(
            self,
            "expected_stream_versions",
            MappingProxyType(expected),
        )
        object.__setattr__(self, "events", events)
        CommandResult(
            response_schema_version=self.response_schema_version,
            status_code=self.status_code,
            body=self.body,
        )

    def to_result(self, appended: AppendBatchResult) -> CommandResult:
        return CommandResult(
            response_schema_version=self.response_schema_version,
            status_code=self.status_code,
            body=self.body,
            first_book_position=appended.positions.start,
            last_book_position=appended.positions.stop - 1,
        )


class LedgerCommitter:
    """The sole runtime coordinator for event append plus sync projection."""

    def __init__(
        self,
        *,
        event_store: PostgresEventStore | None = None,
        projector: SynchronousProjector | None = None,
    ) -> None:
        self._event_store = event_store or PostgresEventStore()
        self._projector = projector or SynchronousProjector()

    def execute_under_book_lock(
        self,
        session: Session,
        book_id: UUID,
    ) -> LockedBookHead:
        head = session.execute(
            select(BookEventHeadRecord)
            .where(BookEventHeadRecord.book_id == book_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if head is None:
            raise BookEventHeadNotFound("book event head not found")
        write_state = session.scalar(
            select(BookRecord.write_state).where(BookRecord.book_id == book_id)
        )
        if write_state is None:
            raise BookEventHeadNotFound("book not found")
        if write_state != "active":
            raise BookWritePaused("Book financial writes are paused")
        transaction = session.get_transaction()
        if transaction is None:
            raise LedgerWriteBoundaryError("Book lock requires an active transaction")
        lock_token = uuid4()
        capabilities = session.info.setdefault(
            "track_anywhere_v2_book_lock_capabilities", {}
        )
        capabilities[book_id] = (lock_token, transaction)
        return LockedBookHead(
            book_id=head.book_id,
            last_position=head.last_position,
            last_hash=head.last_hash,
            _lock_token=lock_token,
        )

    def append_and_project(
        self,
        session: Session,
        *,
        locked_head: LockedBookHead,
        expected_stream_versions: Mapping[StreamKey, int],
        events: Sequence[PendingEvent],
    ) -> AppendBatchResult:
        if type(locked_head) is not LockedBookHead:
            raise LedgerWriteBoundaryError("a locked Book head is required")
        transaction = session.get_transaction()
        capabilities = session.info.get("track_anywhere_v2_book_lock_capabilities", {})
        capability = capabilities.get(locked_head.book_id)
        if (
            transaction is None
            or capability is None
            or capability[0] != locked_head._lock_token
            or capability[1] is not transaction
        ):
            raise LedgerWriteBoundaryError(
                "locked Book head must be used in the same transaction"
            )
        current_head = session.get(BookEventHeadRecord, locked_head.book_id)
        if current_head is None or (
            current_head.last_position,
            current_head.last_hash,
        ) != (locked_head.last_position, locked_head.last_hash):
            raise LedgerWriteBoundaryError("locked Book head changed before append")

        appended = self._event_store._append_batch(
            session,
            book_id=locked_head.book_id,
            expected_stream_versions=expected_stream_versions,
            events=events,
        )
        stored_events = tuple(
            session.scalars(
                select(LedgerEventRecord)
                .where(
                    LedgerEventRecord.book_id == locked_head.book_id,
                    LedgerEventRecord.event_id.in_(appended.event_ids),
                )
                .order_by(LedgerEventRecord.book_position)
            )
        )
        if tuple(event.event_id for event in stored_events) != appended.event_ids:
            raise LedgerWriteBoundaryError("appended event batch could not be reloaded")
        for stored in stored_events:
            self._projector.apply_stored(session, stored)
        session.flush()
        capabilities.pop(locked_head.book_id, None)
        return appended


__all__ = [
    "BookWritePaused",
    "LedgerCommitter",
    "LedgerWriteBoundaryError",
    "LedgerWritePlan",
    "LockedBookHead",
]
