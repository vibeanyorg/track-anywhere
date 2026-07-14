from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.monthly import (
    post_classified_expense,
    seed_monthly_scenario,
)
from track_anywhere.infrastructure.db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionFailureRecord,
)
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    EventStreamHeadRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.monthly_summary import (
    MonthlyCategorySummaryRecord,
)
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.serialization.canonical_json import EventHashEnvelope, event_hash


def _rows(engine, book_id):
    with Session(engine) as session:
        return tuple(
            (
                row.period_start,
                row.category_id,
                row.asset_code,
                row.line_kind,
                int(row.units),
                row.as_of_book_position,
            )
            for row in session.scalars(
                select(MonthlyCategorySummaryRecord)
                .where(MonthlyCategorySummaryRecord.book_id == book_id)
                .order_by(MonthlyCategorySummaryRecord.period_start)
            )
        )


def test_projector_advances_one_book_checkpoint_atomically_and_is_idempotent(
    pg_engine,
) -> None:
    scenario = seed_monthly_scenario(pg_engine)
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 7, 10, tzinfo=UTC),
        amount="12.34",
    )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    worker = AsyncProjectionWorker(factory, batch_size=100)

    first = worker.run_once(scenario.journal.book_id)
    rows = _rows(pg_engine, scenario.journal.book_id)
    second = worker.run_once(scenario.journal.book_id)

    assert first.processed_events == 2
    assert first.last_book_position == 2
    assert second.processed_events == 0
    assert rows == (
        (
            date(2026, 7, 1),
            scenario.category_id,
            "USD",
            "expense",
            1234,
            2,
        ),
    )
    with Session(pg_engine) as session:
        checkpoint = session.get(
            ProjectionCheckpointRecord,
            ("monthly_category_summary", 1, scenario.journal.book_id),
        )
        assert checkpoint is not None and checkpoint.last_book_position == 2


def test_unknown_event_contract_records_failure_and_pauses_book_projection(
    pg_engine,
) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:unknown-event")
    event_id = uuid4()
    stream_id = uuid4()
    command_id = uuid4()
    correlation_id = uuid4()
    effective_at = datetime(2026, 7, 10, tzinfo=UTC)
    payload = {"future": "contract"}
    hashed = event_hash(
        EventHashEnvelope(
            event_id=event_id,
            book_id=scenario.journal.book_id,
            book_position=1,
            global_sequence=1,
            stream_type="future",
            stream_id=stream_id,
            stream_version=1,
            event_type="future.unknown",
            event_schema_version=1,
            command_id=command_id,
            actor_subject_id=scenario.journal.actor_subject_id,
            correlation_id=correlation_id,
            causation_event_id=None,
            effective_at=effective_at,
            recorded_at=effective_at,
            previous_hash=bytes(32),
        ),
        payload,
    )
    with Session(pg_engine) as session, session.begin():
        record = LedgerEventRecord(
            event_id=event_id,
            book_id=scenario.journal.book_id,
            book_position=1,
            stream_type="future",
            stream_id=stream_id,
            stream_version=1,
            event_type="future.unknown",
            event_schema_version=1,
            command_id=command_id,
            actor_subject_id=scenario.journal.actor_subject_id,
            correlation_id=correlation_id,
            causation_event_id=None,
            effective_at=effective_at,
            payload=payload,
            previous_hash=bytes(32),
            event_hash=hashed,
        )
        session.add(record)
        session.flush([record])
        session.add(
            EventStreamHeadRecord(
                book_id=scenario.journal.book_id,
                stream_type="future",
                stream_id=stream_id,
                last_version=1,
                last_book_position=1,
                last_event_id=event_id,
            )
        )
        head = session.get(BookEventHeadRecord, scenario.journal.book_id)
        assert head is not None
        head.last_position = 1
        head.last_hash = hashed

    worker = AsyncProjectionWorker(sessionmaker(pg_engine, expire_on_commit=False))
    first = worker.run_once(scenario.journal.book_id)
    second = worker.run_once(scenario.journal.book_id)

    assert first.paused is True and first.last_book_position == 0
    assert second.paused is True and second.last_book_position == 0
    with Session(pg_engine) as session:
        failures = tuple(
            session.scalars(
                select(ProjectionFailureRecord).where(
                    ProjectionFailureRecord.book_id == scenario.journal.book_id
                )
            )
        )
        assert len(failures) == 1
        assert failures[0].last_error_code == "unknown_event_contract"
        assert (
            session.get(
                ProjectionCheckpointRecord,
                ("monthly_category_summary", 1, scenario.journal.book_id),
            )
            is None
        )
