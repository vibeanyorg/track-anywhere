from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...domain.reporting.events import ReportingLinesAssigned, ReportingLinesCleared
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.async_projection import ProjectionDirtyPeriodRecord
from ..db.models.projections import (
    JournalTransactionRecord,
    TransactionReversalRecord,
)
from .checkpoints import LockedProjectionState, PROJECTION_NAME, PROJECTOR_VERSION
from .event_reader import StoredEventSnapshot


def month_start(value: date) -> date:
    return value.replace(day=1)


def next_month(value: date) -> date:
    return date(
        value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1
    )


def utc_date(value: datetime) -> date:
    """Return the UTC calendar date for an aware financial instant."""

    if not isinstance(value, datetime):
        raise TypeError("financial instant must be a datetime")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC).date()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("financial instant must be timezone-aware") from None


def mark_dirty_periods(
    session: Session,
    state: LockedProjectionState,
    events: tuple[StoredEventSnapshot, ...],
) -> tuple[date, ...]:
    latest_by_period: dict[date, StoredEventSnapshot] = {}
    for event in events:
        for period in _affected_periods(session, event):
            previous = latest_by_period.get(period)
            if previous is None or event.book_position > previous.book_position:
                latest_by_period[period] = event
    for period, event in latest_by_period.items():
        statement = insert(ProjectionDirtyPeriodRecord).values(
            projection_name=PROJECTION_NAME,
            projector_version=PROJECTOR_VERSION,
            book_id=event.book_id,
            generation=state.generation.generation,
            period_start=period,
            period_end=next_month(period),
            source_event_id=event.event_id,
            source_book_position=event.book_position,
        )
        statement = statement.on_conflict_do_update(
            index_elements=(
                "projection_name",
                "projector_version",
                "book_id",
                "generation",
                "period_start",
                "period_end",
            ),
            set_={
                "source_event_id": statement.excluded.source_event_id,
                "source_book_position": statement.excluded.source_book_position,
            },
            where=(
                ProjectionDirtyPeriodRecord.source_book_position
                < statement.excluded.source_book_position
            ),
        )
        session.execute(statement)
    return tuple(sorted(latest_by_period))


def _affected_periods(
    session: Session,
    event: StoredEventSnapshot,
) -> tuple[date, ...]:
    payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
        event.event_type,
        event.event_schema_version,
        event.payload,
    )
    if type(payload) not in {ReportingLinesAssigned, ReportingLinesCleared}:
        return (month_start(utc_date(event.effective_at)),)

    transaction = session.get(
        JournalTransactionRecord,
        (event.book_id, payload.transaction_id),
    )
    if transaction is None:
        raise RuntimeError("reporting event target transaction is unavailable")
    periods = {month_start(utc_date(transaction.effective_at))}
    reversal_id = session.scalar(
        select(TransactionReversalRecord.reversal_transaction_id).where(
            TransactionReversalRecord.book_id == event.book_id,
            TransactionReversalRecord.original_transaction_id
            == payload.transaction_id,
        )
    )
    if reversal_id is not None:
        reversal = session.get(
            JournalTransactionRecord,
            (event.book_id, reversal_id),
        )
        if reversal is None:
            raise RuntimeError("reporting event reversal transaction is unavailable")
        periods.add(month_start(utc_date(reversal.effective_at)))
    return tuple(sorted(periods))


def clear_dirty_periods(
    session: Session,
    state: LockedProjectionState,
    periods: tuple[date, ...],
) -> None:
    if not periods:
        return
    session.execute(
        delete(ProjectionDirtyPeriodRecord).where(
            ProjectionDirtyPeriodRecord.projection_name == PROJECTION_NAME,
            ProjectionDirtyPeriodRecord.projector_version == PROJECTOR_VERSION,
            ProjectionDirtyPeriodRecord.book_id == state.generation.book_id,
            ProjectionDirtyPeriodRecord.generation == state.generation.generation,
            ProjectionDirtyPeriodRecord.period_start.in_(periods),
        )
    )


__all__ = [
    "clear_dirty_periods",
    "mark_dirty_periods",
    "month_start",
    "next_month",
    "utc_date",
]
