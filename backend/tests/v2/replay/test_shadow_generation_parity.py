from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.monthly import (
    post_classified_expense,
    seed_monthly_scenario,
)
from track_anywhere.infrastructure.projections.monthly_summary import (
    cold_replay_monthly_summary,
    read_monthly_summary,
)
from track_anywhere.infrastructure.projections.rebuild import ShadowProjectionRebuilder
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker


def test_swapped_generation_equals_cold_replay_for_every_period(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:shadow-parity")
    for month, amount in ((1, "1.00"), (3, "2.00"), (7, "3.00")):
        post_classified_expense(
            pg_engine,
            scenario,
            effective_at=datetime(2026, month, 10, tzinfo=UTC),
            amount=amount,
        )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    AsyncProjectionWorker(factory).run_once(scenario.journal.book_id)
    ShadowProjectionRebuilder(factory).rebuild_book(scenario.journal.book_id)

    with Session(pg_engine) as session:
        cold = cold_replay_monthly_summary(session, scenario.journal.book_id)
        active = {
            period: read_monthly_summary(
                session,
                scenario.journal.book_id,
                period_start=period,
            )
            for period in (date(2026, 1, 1), date(2026, 3, 1), date(2026, 7, 1))
        }
    assert active == cold
