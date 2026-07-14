from __future__ import annotations

from datetime import UTC, datetime, timedelta
import multiprocessing
import queue
import traceback
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.domain.investments.events import InvestmentLotAcquired
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    SynchronousProjectionAppliedEventRecord,
)
from track_anywhere.serialization.canonical_json import EventHashEnvelope, event_hash


ZERO_HASH = bytes(32)


def _append_worker(
    runtime_url: str,
    book_id_text: str,
    worker_number: int,
    count: int,
    ready,
    start,
    results,
) -> None:
    engine = create_engine(runtime_url, pool_pre_ping=True)
    try:
        book_id = UUID(book_id_text)
        ready.put(worker_number)
        if not start.wait(timeout=30):
            raise RuntimeError("append worker start gate timed out")
        for index in range(count):
            stream_id = uuid5(
                NAMESPACE_URL, f"track-anywhere-v2:{worker_number}:{index}:stream"
            )
            pending = PendingEvent(
                event_id=uuid5(
                    NAMESPACE_URL,
                    f"track-anywhere-v2:{worker_number}:{index}:event",
                ),
                stream_type="investment_lot",
                stream_id=stream_id,
                payload=InvestmentLotAcquired(
                    transaction_id=uuid5(
                        NAMESPACE_URL,
                        f"track-anywhere-v2:{worker_number}:{index}:transaction",
                    ),
                    lot_id=stream_id,
                    instrument_asset_code="AAPL",
                    settlement_asset_code="USD",
                    quantity_units="10",
                    cost_units="1000",
                ),
                command_id=uuid5(
                    NAMESPACE_URL,
                    f"track-anywhere-v2:{worker_number}:{index}:command",
                ),
                actor_subject_id=f"worker:{worker_number}",
                correlation_id=uuid5(
                    NAMESPACE_URL,
                    f"track-anywhere-v2:{worker_number}:{index}:correlation",
                ),
                causation_event_id=None,
                effective_at=datetime(2026, 7, 14, tzinfo=UTC)
                + timedelta(microseconds=(worker_number * count) + index),
            )
            with Session(engine) as session, session.begin():
                PostgresEventStore()._append_batch(
                    session,
                    book_id=book_id,
                    expected_stream_versions={("investment_lot", stream_id): 0},
                    events=(pending,),
                )
                # Infrastructure-only append test: acknowledge the now-sync-required
                # lot event without exercising its real synchronous projector.
                session.add(
                    SynchronousProjectionAppliedEventRecord(
                        book_id=book_id,
                        event_id=pending.event_id,
                        projection_version=1,
                    )
                )
        results.put(("ok", worker_number))
    except BaseException:
        results.put(("error", traceback.format_exc()))
    finally:
        engine.dispose()


def _assert_hash_chain(records: tuple[LedgerEventRecord, ...]) -> None:
    previous_hash = ZERO_HASH
    for expected_position, record in enumerate(records, start=1):
        assert record.book_position == expected_position
        assert record.previous_hash == previous_hash
        assert record.event_hash == event_hash(
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
        previous_hash = record.event_hash


def test_two_processes_append_100_events_to_one_book_without_chain_gaps(
    migrated_postgres_database,
) -> None:
    runtime_url = migrated_postgres_database.runtime_url
    book_id = uuid5(NAMESPACE_URL, "track-anywhere-v2:concurrent-book")
    engine = create_engine(runtime_url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(
            text("""
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Concurrent Book', null, 'active')
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            insert into book_event_heads (book_id, last_position, last_hash)
            values (:book_id, 0, :zero_hash)
            """),
            {"book_id": book_id, "zero_hash": ZERO_HASH},
        )

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_append_worker,
            args=(runtime_url, str(book_id), worker_number, 50, ready, start, results),
        )
        for worker_number in range(2)
    ]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=30), ready.get(timeout=30)} == {0, 1}
        start.set()
        outcomes = [results.get(timeout=90), results.get(timeout=90)]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes)
        assert sorted(outcomes) == [("ok", 0), ("ok", 1)]
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        for channel in (ready, results):
            try:
                channel.close()
                channel.join_thread()
            except (OSError, queue.Empty):
                pass

    with Session(engine) as session:
        records = tuple(
            session.scalars(
                select(LedgerEventRecord)
                .where(LedgerEventRecord.book_id == book_id)
                .order_by(LedgerEventRecord.book_position)
            )
        )
        head = session.get(BookEventHeadRecord, book_id)
    engine.dispose()

    assert len(records) == 100
    _assert_hash_chain(records)
    assert head is not None
    assert head.last_position == 100
    assert head.last_hash == records[-1].event_hash
