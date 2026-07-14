from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.monthly import (
    post_classified_expense,
    seed_monthly_scenario,
)
from track_anywhere.infrastructure.projections.monthly_summary import (
    cold_replay_monthly_summary,
    read_monthly_summary,
)
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.domain.journal.events import ReversalReasonCode
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def test_late_effective_event_rebuilds_only_its_historical_period(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine)
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 7, 10, tzinfo=UTC),
        amount="20.00",
    )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    worker = AsyncProjectionWorker(factory)
    worker.run_once(scenario.journal.book_id)
    with Session(pg_engine) as session:
        july_before = read_monthly_summary(
            session, scenario.journal.book_id, period_start=date(2026, 7, 1)
        )

    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 1, 10, tzinfo=UTC),
        amount="3.00",
    )
    worker.run_once(scenario.journal.book_id)

    with Session(pg_engine) as session:
        january = read_monthly_summary(
            session, scenario.journal.book_id, period_start=date(2026, 1, 1)
        )
        july_after = read_monthly_summary(
            session, scenario.journal.book_id, period_start=date(2026, 7, 1)
        )
        cold = cold_replay_monthly_summary(session, scenario.journal.book_id)
    assert january == cold[date(2026, 1, 1)]
    assert july_after == july_before == cold[date(2026, 7, 1)]


def test_late_reversal_converges_its_effective_period_to_cold_replay(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:monthly-reversal")
    original_id = post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 1, 10, tzinfo=UTC),
        amount="8.00",
    )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    worker = AsyncProjectionWorker(factory)
    worker.run_once(scenario.journal.book_id)

    execute_reverse_transaction(
        ReverseTransactionCommand(
            book_id=scenario.journal.book_id,
            command_id=uuid4(),
            reversal_transaction_id=uuid4(),
            reverses_transaction_id=original_id,
            expected_stream_version=0,
            reason_code=ReversalReasonCode.USER_CORRECTION,
            effective_at=datetime(2026, 2, 2, tzinfo=UTC),
        ),
        raw_key="monthly-late-reversal",
        actor=scenario.actor,
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
    )
    worker.run_once(scenario.journal.book_id)

    with Session(pg_engine) as session:
        january = read_monthly_summary(
            session, scenario.journal.book_id, period_start=date(2026, 1, 1)
        )
        february = read_monthly_summary(
            session, scenario.journal.book_id, period_start=date(2026, 2, 1)
        )
        cold = cold_replay_monthly_summary(session, scenario.journal.book_id)
    assert january == cold[date(2026, 1, 1)]
    assert february == cold[date(2026, 2, 1)]
    assert january[0].units == 800
    assert february[0].units == -800
