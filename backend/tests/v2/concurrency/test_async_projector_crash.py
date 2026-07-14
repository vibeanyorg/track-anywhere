from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.monthly import (
    post_classified_expense,
    seed_monthly_scenario,
)
from track_anywhere.infrastructure.db.models.async_projection import (
    ProjectionCheckpointRecord,
)
from track_anywhere.infrastructure.db.models.monthly_summary import (
    MonthlyCategorySummaryRecord,
)
from track_anywhere.infrastructure.projections.worker import (
    AsyncProjectionWorker,
    ProjectionWorkerHooks,
)


def test_crash_after_projection_apply_rolls_back_rows_and_checkpoint(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine)
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 3, 1, tzinfo=UTC),
        amount="4.00",
    )
    factory = sessionmaker(pg_engine, expire_on_commit=False)

    def crash() -> None:
        raise RuntimeError("simulated projector termination")

    crashing = AsyncProjectionWorker(
        factory,
        hooks=ProjectionWorkerHooks(after_projection_before_checkpoint=crash),
    )
    with pytest.raises(RuntimeError, match="simulated projector termination"):
        crashing.run_once(scenario.journal.book_id)

    with Session(pg_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(MonthlyCategorySummaryRecord)
            )
            == 0
        )
        assert (
            session.get(
                ProjectionCheckpointRecord,
                ("monthly_category_summary", 1, scenario.journal.book_id),
            )
            is None
        )

    result = AsyncProjectionWorker(factory).run_once(scenario.journal.book_id)
    assert result.last_book_position == 2
