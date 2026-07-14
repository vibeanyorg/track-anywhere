from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.domain.investments.events import InvestmentLotAcquired
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    SynchronousProjectionAppliedEventRecord,
)
from track_anywhere.infrastructure.projections.event_reader import PerBookEventReader


ZERO_HASH = bytes(32)


def _seed_book(pg_engine, name: str) -> UUID:
    book_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, :name, null, 'active')
            """),
            {"book_id": book_id, "name": name},
        )
        connection.execute(
            text("""
            insert into book_event_heads (book_id, last_position, last_hash)
            values (:book_id, 0, :zero_hash)
            """),
            {"book_id": book_id, "zero_hash": ZERO_HASH},
        )
    return book_id


def _pending(stream_id: UUID, *, command_id: UUID) -> PendingEvent:
    return PendingEvent(
        event_id=uuid4(),
        stream_type="investment_lot",
        stream_id=stream_id,
        payload=InvestmentLotAcquired(
            transaction_id=uuid4(),
            lot_id=stream_id,
            instrument_asset_code="AAPL",
            settlement_asset_code="USD",
            quantity_units="10",
            cost_units="1000",
        ),
        command_id=command_id,
        actor_subject_id="user:reverse-commit",
        correlation_id=uuid4(),
        causation_event_id=None,
        effective_at=datetime(2026, 7, 14, tzinfo=UTC),
    )


def _acknowledge_infrastructure_append(
    session: Session,
    *,
    book_id: UUID,
    event_id: UUID,
) -> None:
    # This test intentionally bypasses LedgerCommitter so it can control commit
    # order; acknowledge the sync event without asserting lot projection behavior.
    session.add(
        SynchronousProjectionAppliedEventRecord(
            book_id=book_id,
            event_id=event_id,
            projection_version=1,
        )
    )


def test_reverse_commit_order_cannot_hide_the_late_book_event(pg_engine) -> None:
    book_a = _seed_book(pg_engine, "Book A")
    book_b = _seed_book(pg_engine, "Book B")
    stream_a = uuid4()
    stream_b = uuid4()
    session_a = Session(pg_engine)
    session_b = Session(pg_engine)
    try:
        event_a = _pending(stream_a, command_id=uuid4())
        event_b = _pending(stream_b, command_id=uuid4())

        # A allocates the earlier diagnostic sequence but remains uncommitted.
        PostgresEventStore()._append_batch(
            session_a,
            book_id=book_a,
            expected_stream_versions={("investment_lot", stream_a): 0},
            events=(event_a,),
        )
        _acknowledge_infrastructure_append(
            session_a,
            book_id=book_a,
            event_id=event_a.event_id,
        )
        sequence_a = session_a.scalar(
            select(LedgerEventRecord.global_sequence).where(
                LedgerEventRecord.event_id == event_a.event_id
            )
        )

        # B allocates a later sequence and commits first.
        PostgresEventStore()._append_batch(
            session_b,
            book_id=book_b,
            expected_stream_versions={("investment_lot", stream_b): 0},
            events=(event_b,),
        )
        _acknowledge_infrastructure_append(
            session_b,
            book_id=book_b,
            event_id=event_b.event_id,
        )
        session_b.commit()

        reader = PerBookEventReader()
        with Session(pg_engine) as projector:
            discovered = reader.discover_book_ids(projector)
            visible_b = reader.read_after(
                projector, book_id=book_b, after_book_position=0, limit=10
            )
            invisible_a = reader.read_after(
                projector, book_id=book_a, after_book_position=0, limit=10
            )
            sequence_b = projector.scalar(
                select(LedgerEventRecord.global_sequence).where(
                    LedgerEventRecord.event_id == event_b.event_id
                )
            )

        assert book_a in discovered and book_b in discovered
        assert [event.event_id for event in visible_b] == [event_b.event_id]
        assert invisible_a == ()
        assert sequence_a is not None and sequence_b is not None
        assert sequence_a < sequence_b

        # A commits after B has already been consumed. Its own checkpoint remains 0,
        # so the next per-Book read necessarily sees A position 1.
        session_a.commit()
        with Session(pg_engine) as projector:
            visible_a = reader.read_after(
                projector, book_id=book_a, after_book_position=0, limit=10
            )
            no_duplicate_b = reader.read_after(
                projector, book_id=book_b, after_book_position=1, limit=10
            )

        assert [(event.book_id, event.book_position) for event in visible_a] == [
            (book_a, 1)
        ]
        assert no_duplicate_b == ()
    finally:
        session_b.rollback()
        session_a.rollback()
        session_b.close()
        session_a.close()
