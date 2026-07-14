from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    pending_posted_event,
    posted_event,
    projection_state,
    seed_journal_scenario,
)
from track_anywhere.application.ledger_committer import (
    BookWritePaused,
    LedgerCommitter,
    LedgerWriteBoundaryError,
)
from track_anywhere.domain.journal.models import PostingSide
from track_anywhere.infrastructure.db.models.catalog import BookRecord
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    SynchronousProjectionAppliedEventRecord,
)
from track_anywhere.infrastructure.projections.synchronous import (
    SynchronousProjector,
)


def test_posted_event_and_all_projections_commit_atomically_and_are_immediately_visible(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    committer = LedgerCommitter()
    with Session(pg_engine) as session, session.begin():
        locked = committer.execute_under_book_lock(session, scenario.book_id)
        appended = committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={
                ("journal_transaction", scenario.transaction_id): 0
            },
            events=(pending_posted_event(scenario),),
        )

    assert appended.positions == range(1, 2)
    with Session(pg_engine) as independent_session:
        state = projection_state(independent_session, scenario)
        balances = {
            record.account_id: record
            for record in independent_session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == scenario.book_id
                )
            )
        }
        stored = independent_session.scalar(
            select(LedgerEventRecord).where(
                LedgerEventRecord.event_id == scenario.event_id
            )
        )

    assert state["transaction"] == (
        scenario.transaction_id,
        scenario.event_id,
        1,
        "standard",
    )
    assert len(state["postings"]) == 2
    assert state["projection_version"] == 1
    assert balances[scenario.debit_account_id].balance_units == Decimal(1000)
    assert balances[scenario.credit_account_id].balance_units == Decimal(-1000)
    assert all(record.as_of_position == 1 for record in balances.values())
    assert stored is not None

    with Session(pg_engine) as session, session.begin():
        duplicate = SynchronousProjector().apply_stored(session, stored)
    assert duplicate.required is True
    assert duplicate.applied is False
    with Session(pg_engine) as session:
        assert projection_state(session, scenario) == state


def test_exception_after_projection_rolls_back_event_marker_and_every_projection(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    committer = LedgerCommitter()

    with pytest.raises(RuntimeError, match="force rollback"):
        with Session(pg_engine) as session, session.begin():
            locked = committer.execute_under_book_lock(session, scenario.book_id)
            committer.append_and_project(
                session,
                locked_head=locked,
                expected_stream_versions={
                    ("journal_transaction", scenario.transaction_id): 0
                },
                events=(pending_posted_event(scenario),),
            )
            raise RuntimeError("force rollback")

    with Session(pg_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == scenario.book_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(JournalTransactionRecord)
                .where(JournalTransactionRecord.book_id == scenario.book_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(JournalPostingRecord)
                .where(JournalPostingRecord.book_id == scenario.book_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(SynchronousProjectionAppliedEventRecord)
                .where(
                    SynchronousProjectionAppliedEventRecord.book_id == scenario.book_id
                )
            )
            == 0
        )
        assert session.get(BookEventHeadRecord, scenario.book_id).last_position == 0


def test_unbalanced_posting_commit_rejects_and_rolls_back_every_write(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    payload = posted_event(
        scenario,
        debit_side=PostingSide.DEBIT,
        credit_side=PostingSide.DEBIT,
    )
    session = Session(pg_engine)
    transaction = session.begin()
    try:
        locked = LedgerCommitter().execute_under_book_lock(session, scenario.book_id)
        LedgerCommitter().append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={
                ("journal_transaction", scenario.transaction_id): 0
            },
            events=(pending_posted_event(scenario, payload=payload),),
        )
        with pytest.raises(DBAPIError):
            transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        session.close()

    with Session(pg_engine) as verification:
        assert projection_state(verification, scenario) == {
            "transaction": None,
            "postings": (),
            "balances": (),
            "references": (),
            "projection_version": None,
        }
        assert (
            verification.scalar(
                select(func.count())
                .select_from(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == scenario.book_id)
            )
            == 0
        )
        assert (
            verification.get(BookEventHeadRecord, scenario.book_id).last_position == 0
        )


def test_locked_head_capability_cannot_be_reused_in_another_transaction(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    committer = LedgerCommitter()
    with Session(pg_engine) as first, first.begin():
        stale_capability = committer.execute_under_book_lock(first, scenario.book_id)

    with pytest.raises(LedgerWriteBoundaryError, match="same transaction"):
        with Session(pg_engine) as second, second.begin():
            committer.append_and_project(
                second,
                locked_head=stale_capability,
                expected_stream_versions={
                    ("journal_transaction", scenario.transaction_id): 0
                },
                events=(pending_posted_event(scenario),),
            )

    with Session(pg_engine) as session:
        assert session.get(BookEventHeadRecord, scenario.book_id).last_position == 0


def test_book_write_state_is_refreshed_after_waiting_for_the_head_lock(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    stale_reader = Session(pg_engine)
    try:
        stale_book = stale_reader.get(BookRecord, scenario.book_id)
        assert stale_book is not None and stale_book.write_state == "active"
        with Session(pg_engine) as auditor, auditor.begin():
            auditor.execute(
                select(BookEventHeadRecord)
                .where(BookEventHeadRecord.book_id == scenario.book_id)
                .with_for_update()
            ).scalar_one()
            auditor.execute(
                update(BookRecord)
                .where(BookRecord.book_id == scenario.book_id)
                .values(write_state="paused_integrity")
            )

        with pytest.raises(BookWritePaused):
            LedgerCommitter().execute_under_book_lock(stale_reader, scenario.book_id)
        assert stale_book.write_state == "active"
    finally:
        stale_reader.rollback()
        stale_reader.close()


def test_book_head_snapshot_is_refreshed_when_the_session_cached_an_older_head(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    stale_reader = Session(pg_engine)
    try:
        stale_head = stale_reader.get(BookEventHeadRecord, scenario.book_id)
        assert stale_head is not None and stale_head.last_position == 0
        with Session(pg_engine) as writer, writer.begin():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(writer, scenario.book_id)
            committer.append_and_project(
                writer,
                locked_head=locked,
                expected_stream_versions={
                    ("journal_transaction", scenario.transaction_id): 0
                },
                events=(pending_posted_event(scenario),),
            )

        refreshed = LedgerCommitter().execute_under_book_lock(
            stale_reader, scenario.book_id
        )
        assert stale_head.last_position == 1
        assert refreshed.last_position == 1
    finally:
        stale_reader.rollback()
        stale_reader.close()
