from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    pending_posted_event,
    projection_state,
    seed_journal_scenario,
)
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.projections.synchronous import SynchronousProjector


def test_cold_replay_produces_the_same_journal_projection_as_online_commit(
    migrated_postgres_source_target,
) -> None:
    source_database, target_database = migrated_postgres_source_target
    source_engine = create_engine(source_database.runtime_url, pool_pre_ping=True)
    target_engine = create_engine(target_database.runtime_url, pool_pre_ping=True)
    scenario = JournalScenario.create()
    try:
        seed_journal_scenario(source_engine, scenario)
        seed_journal_scenario(target_engine, scenario)
        pending = pending_posted_event(scenario)

        with Session(source_engine) as session, session.begin():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(session, scenario.book_id)
            committer.append_and_project(
                session,
                locked_head=locked,
                expected_stream_versions={
                    ("journal_transaction", scenario.transaction_id): 0
                },
                events=(pending,),
            )

        with Session(target_engine) as session, session.begin():
            PostgresEventStore()._append_batch(
                session,
                book_id=scenario.book_id,
                expected_stream_versions={
                    ("journal_transaction", scenario.transaction_id): 0
                },
                events=(pending,),
            )
            stored = session.scalar(
                select(LedgerEventRecord).where(
                    LedgerEventRecord.event_id == scenario.event_id
                )
            )
            assert stored is not None
            applied = SynchronousProjector().apply_stored(session, stored)
            assert applied.required is True and applied.applied is True

        with Session(source_engine) as source, Session(target_engine) as target:
            assert projection_state(source, scenario) == projection_state(
                target, scenario
            )
    finally:
        target_engine.dispose()
        source_engine.dispose()
