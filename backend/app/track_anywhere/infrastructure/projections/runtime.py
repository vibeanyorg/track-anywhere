from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import anyio
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from ..db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionFailureRecord,
)
from ..db.models.event_store import BookEventHeadRecord
from .checkpoints import PROJECTION_NAME, PROJECTOR_VERSION
from .worker import AsyncProjectionWorker, ProjectionRunResult


PROJECTION_ADVISORY_LOCK_KEY = 0x545241434B50524F

logger = logging.getLogger(__name__)


class ProjectionWorker(Protocol):
    def run_once(self, book_id: UUID) -> ProjectionRunResult: ...


PendingBookIds = Callable[[Session], tuple[UUID, ...]]


class ProjectionRuntime:
    """Continuously advances rebuildable projections with one cluster leader."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        worker: ProjectionWorker | None = None,
        pending_book_ids: PendingBookIds | None = None,
        poll_seconds: float = 2.0,
    ) -> None:
        if (
            type(poll_seconds) not in {int, float}
            or not math.isfinite(poll_seconds)
            or not 0.1 <= poll_seconds <= 300
        ):
            raise ValueError("poll_seconds is outside its allowed range")
        self._session_factory = session_factory
        self._worker = worker or AsyncProjectionWorker(session_factory)
        self._pending_book_ids = pending_book_ids or _load_pending_book_ids
        self._poll_seconds = float(poll_seconds)

    def run_cycle(self) -> int:
        processed_events = 0
        with self._session_factory() as coordinator:
            acquired = coordinator.scalar(
                select(func.pg_try_advisory_lock(PROJECTION_ADVISORY_LOCK_KEY))
            )
            if acquired is not True:
                return 0
            try:
                book_ids = self._pending_book_ids(coordinator)
                for book_id in book_ids:
                    try:
                        result = self._worker.run_once(book_id)
                    except Exception:
                        logger.exception(
                            "monthly projection failed for book %s",
                            book_id,
                        )
                        continue
                    processed_events += result.processed_events
            finally:
                coordinator.scalar(
                    select(func.pg_advisory_unlock(PROJECTION_ADVISORY_LOCK_KEY))
                )
        return processed_events

    async def run_forever(self) -> None:
        while True:
            try:
                processed_events = await anyio.to_thread.run_sync(self.run_cycle)
            except Exception:
                logger.exception("monthly projection runtime cycle failed")
                processed_events = 0
            if processed_events:
                await anyio.lowlevel.checkpoint()
            else:
                await anyio.sleep(self._poll_seconds)


def _load_pending_book_ids(session: Session) -> tuple[UUID, ...]:
    checkpoint_match = and_(
        ProjectionCheckpointRecord.projection_name == PROJECTION_NAME,
        ProjectionCheckpointRecord.projector_version == PROJECTOR_VERSION,
        ProjectionCheckpointRecord.book_id == BookEventHeadRecord.book_id,
    )
    paused_failure = exists(
        select(ProjectionFailureRecord.failure_id).where(
            ProjectionFailureRecord.projection_name == PROJECTION_NAME,
            ProjectionFailureRecord.projector_version == PROJECTOR_VERSION,
            ProjectionFailureRecord.book_id == BookEventHeadRecord.book_id,
            ProjectionFailureRecord.retry_state == "paused",
        )
    )
    statement = (
        select(BookEventHeadRecord.book_id)
        .outerjoin(ProjectionCheckpointRecord, checkpoint_match)
        .where(
            BookEventHeadRecord.last_position
            > func.coalesce(ProjectionCheckpointRecord.last_book_position, 0),
            ~paused_failure,
        )
        .order_by(BookEventHeadRecord.book_id)
    )
    return tuple(session.scalars(statement))


__all__ = [
    "PROJECTION_ADVISORY_LOCK_KEY",
    "ProjectionRuntime",
]
