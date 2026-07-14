from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.domain.investments.events import InvestmentLotAcquired
from track_anywhere.infrastructure.db.event_store import (
    AppendBatchValidationError,
    PostgresEventStore,
    StreamVersionConflict,
)
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    EventStreamHeadRecord,
    LedgerEventRecord,
)
from track_anywhere.serialization.canonical_json import EventHashEnvelope, event_hash


ZERO_HASH = bytes(32)
EFFECTIVE_AT = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _seed_book(pg_engine, *, name: str = "Append test") -> UUID:
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


def _pending_event(
    *,
    stream_type: str,
    stream_id: UUID,
    event_id: UUID | None = None,
    offset: int = 0,
) -> PendingEvent:
    return PendingEvent(
        event_id=event_id or uuid4(),
        stream_type=stream_type,
        stream_id=stream_id,
        payload=InvestmentLotAcquired(
            transaction_id=uuid4(),
            lot_id=stream_id,
            instrument_asset_code="AAPL",
            settlement_asset_code="USD",
            quantity_units="10",
            cost_units="1000",
        ),
        command_id=uuid4(),
        actor_subject_id="user:test-append",
        correlation_id=uuid4(),
        causation_event_id=None,
        effective_at=EFFECTIVE_AT + timedelta(microseconds=offset),
    )


def _append_batch_with_projection_markers(
    session: Session,
    *,
    book_id: UUID,
    expected_stream_versions: dict[tuple[str, UUID], int],
    events: tuple[PendingEvent, ...],
):
    result = PostgresEventStore()._append_batch(
        session,
        book_id=book_id,
        expected_stream_versions=expected_stream_versions,
        events=events,
    )
    session.execute(
        text(
            """
            insert into synchronous_projection_applied_events (
                book_id, event_id, projection_version
            ) values (:book_id, :event_id, 1)
            """
        ),
        [
            {"book_id": book_id, "event_id": event_id}
            for event_id in result.event_ids
        ],
    )
    return result


def _recomputed_hash(record: LedgerEventRecord) -> bytes:
    return event_hash(
        EventHashEnvelope(
            event_id=record.event_id,
            book_id=record.book_id,
            book_position=record.book_position,
            global_sequence=record.global_sequence,
            stream_type=record.stream_type,
            stream_id=record.stream_id,
            stream_version=record.stream_version,
            event_type=record.event_type,
            event_schema_version=record.event_schema_version,
            command_id=record.command_id,
            actor_subject_id=record.actor_subject_id,
            correlation_id=record.correlation_id,
            causation_event_id=record.causation_event_id,
            effective_at=record.effective_at,
            recorded_at=record.recorded_at,
            previous_hash=record.previous_hash,
        ),
        record.payload,
    )


def test_append_batch_builds_book_hash_chain_and_per_type_stream_versions(
    pg_engine,
) -> None:
    book_id = _seed_book(pg_engine)
    shared_stream_id = uuid4()
    events = (
        _pending_event(
            stream_type="investment_lot", stream_id=shared_stream_id, offset=1
        ),
        _pending_event(
            stream_type="investment_lot", stream_id=shared_stream_id, offset=2
        ),
        _pending_event(
            stream_type="investment_account", stream_id=shared_stream_id, offset=3
        ),
    )

    with Session(pg_engine) as session, session.begin():
        result = _append_batch_with_projection_markers(
            session,
            book_id=book_id,
            expected_stream_versions={
                ("investment_lot", shared_stream_id): 0,
                ("investment_account", shared_stream_id): 0,
            },
            events=events,
        )

    assert result.positions == range(1, 4)
    assert result.event_ids == tuple(event.event_id for event in events)

    with Session(pg_engine) as session:
        records = tuple(
            session.scalars(
                select(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == book_id)
                .order_by(LedgerEventRecord.book_position)
            )
        )
        head = session.get(BookEventHeadRecord, book_id)
        stream_heads = {
            (record.stream_type, record.stream_id): record
            for record in session.scalars(
                select(EventStreamHeadRecord).where(
                    EventStreamHeadRecord.book_id == book_id
                )
            )
        }

    assert [record.book_position for record in records] == [1, 2, 3]
    assert [record.stream_version for record in records] == [1, 2, 1]
    assert records[0].previous_hash == ZERO_HASH
    assert records[1].previous_hash == records[0].event_hash
    assert records[2].previous_hash == records[1].event_hash
    assert all(record.event_hash == _recomputed_hash(record) for record in records)
    assert all(record.global_sequence > 0 for record in records)
    assert all(record.recorded_at is not None for record in records)
    assert head is not None
    assert (head.last_position, head.last_hash) == (3, records[-1].event_hash)
    assert result.terminal_hash == head.last_hash
    assert stream_heads[("investment_lot", shared_stream_id)].last_version == 2
    assert stream_heads[("investment_account", shared_stream_id)].last_version == 1


def test_expected_stream_version_conflict_is_typed_and_writes_nothing(
    pg_engine,
) -> None:
    book_id = _seed_book(pg_engine)
    stream_id = uuid4()
    with Session(pg_engine) as session, session.begin():
        _append_batch_with_projection_markers(
            session,
            book_id=book_id,
            expected_stream_versions={("investment_lot", stream_id): 0},
            events=(_pending_event(stream_type="investment_lot", stream_id=stream_id),),
        )

    with pytest.raises(StreamVersionConflict) as error_info:
        with Session(pg_engine) as session, session.begin():
            _append_batch_with_projection_markers(
                session,
                book_id=book_id,
                expected_stream_versions={("investment_lot", stream_id): 0},
                events=(
                    _pending_event(
                        stream_type="investment_lot", stream_id=stream_id, offset=1
                    ),
                ),
            )

    assert error_info.value.stream_key == ("investment_lot", stream_id)
    assert error_info.value.expected_version == 0
    assert error_info.value.actual_version == 1
    with Session(pg_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == book_id)
            )
            == 1
        )
        assert session.get(BookEventHeadRecord, book_id).last_position == 1


def test_expected_versions_must_exactly_cover_touched_streams(pg_engine) -> None:
    book_id = _seed_book(pg_engine)
    stream_id = uuid4()
    with pytest.raises(AppendBatchValidationError):
        with Session(pg_engine) as session, session.begin():
            _append_batch_with_projection_markers(
                session,
                book_id=book_id,
                expected_stream_versions={},
                events=(
                    _pending_event(stream_type="investment_lot", stream_id=stream_id),
                ),
            )

    with Session(pg_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == book_id)
            )
            == 0
        )


def test_database_failure_rolls_back_the_whole_batch_and_all_heads(pg_engine) -> None:
    book_id = _seed_book(pg_engine)
    stream_id = uuid4()
    duplicate_event_id = uuid4()
    events = (
        _pending_event(
            stream_type="investment_lot",
            stream_id=stream_id,
            event_id=duplicate_event_id,
        ),
        _pending_event(
            stream_type="investment_lot",
            stream_id=stream_id,
            event_id=duplicate_event_id,
            offset=1,
        ),
    )

    with pytest.raises(IntegrityError):
        with Session(pg_engine) as session, session.begin():
            _append_batch_with_projection_markers(
                session,
                book_id=book_id,
                expected_stream_versions={("investment_lot", stream_id): 0},
                events=events,
            )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, book_id)
        assert head is not None
        assert (head.last_position, head.last_hash) == (0, ZERO_HASH)
        assert (
            session.scalar(
                select(func.count())
                .select_from(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == book_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventStreamHeadRecord)
                .where(EventStreamHeadRecord.book_id == book_id)
            )
            == 0
        )


def test_locking_one_book_head_does_not_block_another_book_append(pg_engine) -> None:
    book_a = _seed_book(pg_engine, name="Book A")
    book_b = _seed_book(pg_engine, name="Book B")
    stream_id = uuid4()
    first = Session(pg_engine)
    second = Session(pg_engine)
    try:
        first.execute(
            select(BookEventHeadRecord)
            .where(BookEventHeadRecord.book_id == book_a)
            .with_for_update()
        ).scalar_one()
        second.execute(text("set local lock_timeout = '250ms'"))
        _append_batch_with_projection_markers(
            second,
            book_id=book_b,
            expected_stream_versions={("investment_lot", stream_id): 0},
            events=(_pending_event(stream_type="investment_lot", stream_id=stream_id),),
        )
        second.commit()
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()

    with Session(pg_engine) as session:
        assert session.get(BookEventHeadRecord, book_a).last_position == 0
        assert session.get(BookEventHeadRecord, book_b).last_position == 1
