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
    ProjectionGenerationRecord,
)
from track_anywhere.infrastructure.projections.checkpoints import (
    PROJECTION_NAME,
    PROJECTOR_VERSION,
)
from track_anywhere.infrastructure.projections.rebuild import ShadowProjectionRebuilder
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker


def test_shadow_rebuild_catches_up_writes_and_swaps_atomically(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:shadow-rebuild")
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 1, 5, tzinfo=UTC),
        amount="5.00",
    )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    AsyncProjectionWorker(factory).run_once(scenario.journal.book_id)

    observed_active: list[int] = []

    def append_while_shadow_is_complete() -> None:
        with Session(pg_engine) as session:
            checkpoint = session.get(
                ProjectionCheckpointRecord,
                (PROJECTION_NAME, PROJECTOR_VERSION, scenario.journal.book_id),
            )
            assert checkpoint is not None
            observed_active.append(checkpoint.active_generation)
        post_classified_expense(
            pg_engine,
            scenario,
            effective_at=datetime(2026, 2, 6, tzinfo=UTC),
            amount="7.00",
        )

    result = ShadowProjectionRebuilder(
        factory,
        after_shadow_built=append_while_shadow_is_complete,
    ).rebuild_book(scenario.journal.book_id)

    assert observed_active == [1]
    assert (result.previous_generation, result.active_generation) == (1, 2)
    with Session(pg_engine) as session:
        checkpoint = session.get(
            ProjectionCheckpointRecord,
            (PROJECTION_NAME, PROJECTOR_VERSION, scenario.journal.book_id),
        )
        states = {
            generation.generation: generation.state
            for generation in session.scalars(
                select(ProjectionGenerationRecord).where(
                    ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
                    ProjectionGenerationRecord.projector_version == PROJECTOR_VERSION,
                    ProjectionGenerationRecord.book_id == scenario.journal.book_id,
                )
            )
        }
        assert checkpoint is not None
        assert checkpoint.active_generation == 2
        assert checkpoint.last_book_position == 4
        assert states == {1: "retired", 2: "active"}


def test_interrupted_shadow_generation_is_resumed_not_restarted(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:resume-rebuild")
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 4, 1, tzinfo=UTC),
        amount="9.00",
    )
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    AsyncProjectionWorker(factory).run_once(scenario.journal.book_id)

    def crash() -> None:
        raise RuntimeError("builder terminated after durable shadow")

    with pytest.raises(RuntimeError, match="builder terminated"):
        ShadowProjectionRebuilder(factory, after_shadow_built=crash).rebuild_book(
            scenario.journal.book_id
        )

    with Session(pg_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ProjectionGenerationRecord))
            == 2
        )
    result = ShadowProjectionRebuilder(factory).rebuild_book(scenario.journal.book_id)
    assert result.active_generation == 2
    with Session(pg_engine) as session:
        checkpoint = session.get(
            ProjectionCheckpointRecord,
            (PROJECTION_NAME, PROJECTOR_VERSION, scenario.journal.book_id),
        )
        assert checkpoint is not None and checkpoint.active_generation == 2
