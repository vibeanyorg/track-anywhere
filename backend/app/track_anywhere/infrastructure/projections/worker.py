from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...observability.metrics import LedgerMetrics
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.async_projection import ProjectionFailureRecord
from ..db.models.event_store import BookEventHeadRecord
from .checkpoints import (
    PROJECTION_NAME,
    PROJECTOR_VERSION,
    activate_or_advance,
    lock_projection_state,
)
from .dirty_periods import clear_dirty_periods, mark_dirty_periods
from .event_reader import PerBookEventReader
from .monthly_summary import replace_periods


@dataclass(frozen=True, slots=True)
class ProjectionWorkerHooks:
    after_projection_before_checkpoint: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class ProjectionRunResult:
    processed_events: int
    last_book_position: int
    paused: bool = False


class AsyncProjectionWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        batch_size: int = 500,
        hooks: ProjectionWorkerHooks | None = None,
        event_reader: PerBookEventReader | None = None,
        metrics: LedgerMetrics | None = None,
    ) -> None:
        if type(batch_size) is not int or not 1 <= batch_size <= 10_000:
            raise ValueError("batch_size is outside its allowed range")
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._hooks = hooks or ProjectionWorkerHooks()
        self._reader = event_reader or PerBookEventReader()
        self._metrics = metrics

    def run_once(self, book_id: UUID) -> ProjectionRunResult:
        dirty_period_count = 0
        projection_lag = 0
        failed = False
        with self._session_factory() as session, session.begin():
            from ..db.models.async_projection import ProjectionCheckpointRecord

            paused_failure = session.scalar(
                select(ProjectionFailureRecord.failure_id)
                .where(
                    ProjectionFailureRecord.projection_name == PROJECTION_NAME,
                    ProjectionFailureRecord.projector_version == PROJECTOR_VERSION,
                    ProjectionFailureRecord.book_id == book_id,
                    ProjectionFailureRecord.retry_state == "paused",
                )
                .limit(1)
            )
            checkpoint = session.get(
                ProjectionCheckpointRecord,
                (PROJECTION_NAME, PROJECTOR_VERSION, book_id),
            )
            after = 0 if checkpoint is None else checkpoint.last_book_position
            head = session.get(BookEventHeadRecord, book_id)
            if head is None:
                raise LookupError("Book event head not found")
            if paused_failure is not None:
                result = ProjectionRunResult(0, after, paused=True)
                projection_lag = max(0, head.last_position - after)
                failed = True
            else:
                events = self._reader.read_after(
                    session,
                    book_id=book_id,
                    after_book_position=after,
                    limit=self._batch_size,
                )
                if not events:
                    result = ProjectionRunResult(0, after)
                    projection_lag = max(0, head.last_position - after)
                else:
                    final_position = events[-1].book_position
                    state = lock_projection_state(
                        session,
                        book_id=book_id,
                        target_book_position=final_position,
                    )
                    invalid_event = next(
                        (event for event in events if not _is_registered_event(event)),
                        None,
                    )
                    if invalid_event is not None:
                        session.execute(
                            ProjectionFailureRecord.__table__.insert().values(
                                failure_id=uuid4(),
                                projection_name=PROJECTION_NAME,
                                projector_version=PROJECTOR_VERSION,
                                book_id=book_id,
                                generation=state.generation.generation,
                                source_event_id=invalid_event.event_id,
                                source_book_position=invalid_event.book_position,
                                event_type=invalid_event.event_type,
                                event_schema_version=invalid_event.event_schema_version,
                                failure_kind="unknown_event",
                                retry_state="paused",
                                attempt_count=1,
                                next_retry_at=None,
                                last_error_code="unknown_event_contract",
                            )
                        )
                        result = ProjectionRunResult(0, after, paused=True)
                        projection_lag = max(0, head.last_position - after)
                        failed = True
                    else:
                        periods = mark_dirty_periods(session, state, events)
                        dirty_period_count = len(periods)
                        replace_periods(
                            session,
                            book_id=book_id,
                            generation=state.generation.generation,
                            periods=periods,
                            through_book_position=final_position,
                        )
                        if self._hooks.after_projection_before_checkpoint is not None:
                            self._hooks.after_projection_before_checkpoint()
                        activate_or_advance(
                            session,
                            state,
                            last_book_position=final_position,
                        )
                        clear_dirty_periods(session, state, periods)
                        result = ProjectionRunResult(len(events), final_position)
                        projection_lag = max(0, head.last_position - final_position)
        self._record_metrics(
            result,
            dirty_period_count=dirty_period_count,
            projection_lag=projection_lag,
            failed=failed,
        )
        return result

    def _record_metrics(
        self,
        result: ProjectionRunResult,
        *,
        dirty_period_count: int,
        projection_lag: int,
        failed: bool,
    ) -> None:
        if self._metrics is None:
            return
        if result.processed_events:
            self._metrics.increment(
                "projection.events_processed",
                result.processed_events,
            )
        if dirty_period_count:
            self._metrics.increment(
                "projection.dirty_periods",
                dirty_period_count,
            )
        if failed:
            self._metrics.increment("projection.failures")
        self._metrics.gauge("projection.lag", projection_lag)


def _is_registered_event(event: object) -> bool:
    try:
        PRODUCTION_EVENT_REGISTRY.validate_stored(
            event.event_type,
            event.event_schema_version,
            event.payload,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return True


__all__ = [
    "AsyncProjectionWorker",
    "ProjectionRunResult",
    "ProjectionWorkerHooks",
]
