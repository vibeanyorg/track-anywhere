from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import multiprocessing
import os
import signal
import traceback
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.application.command_bus import execute
from track_anywhere.application.idempotency import (
    AuthorizationScope,
    CommandActor,
    CommandResult,
)
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.domain.investments.events import InvestmentLotAcquired
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.repositories import RowLock
from track_anywhere.infrastructure.db.repositories.auth import AuthRepository
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


ACTOR = CommandActor(subject_id="human:cross-process")
RAW_KEY = "cross-process-secret-key"
EFFECTIVE_AT = datetime(2026, 7, 14, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeCommand:
    book_id: UUID
    command_id: UUID
    stream_id: UUID
    amount: str = "1000"
    operation: str = "fake.cross-process"

    def idempotency_payload(self) -> dict[str, object]:
        return {"amount": self.amount, "stream_id": str(self.stream_id)}


def _command(book_id: UUID) -> FakeCommand:
    return FakeCommand(
        book_id=book_id,
        command_id=uuid5(NAMESPACE_URL, f"receipt:{book_id}:command"),
        stream_id=uuid5(NAMESPACE_URL, f"receipt:{book_id}:stream"),
    )


def _authorize(session, actor, book_id, *, lock_membership):
    membership = AuthRepository(session).get_membership(
        book_id,
        actor.subject_id,
        lock=RowLock.SHARE if lock_membership else RowLock.NONE,
    )
    if membership.status != "active" or "ledger:write" not in membership.scopes:
        raise PermissionError("not currently authorized")
    return AuthorizationScope(
        book_id=book_id,
        actor_subject_id=actor.subject_id,
        role=membership.role,
        scopes=membership.scopes,
    )


def _atomic_handler(command: FakeCommand, uow) -> CommandResult:
    pending = PendingEvent(
        event_id=command.command_id,
        stream_type="investment_lot",
        stream_id=command.stream_id,
        payload=InvestmentLotAcquired(
            transaction_id=command.command_id,
            lot_id=command.stream_id,
            instrument_asset_code="AAPL",
            settlement_asset_code="USD",
            quantity_units="1",
            cost_units=command.amount,
        ),
        command_id=command.command_id,
        actor_subject_id=ACTOR.subject_id,
        correlation_id=command.command_id,
        causation_event_id=None,
        effective_at=EFFECTIVE_AT,
    )
    appended = PostgresEventStore()._append_batch(
        uow.session,
        book_id=command.book_id,
        expected_stream_versions={("investment_lot", command.stream_id): 0},
        events=(pending,),
    )
    return CommandResult(
        response_schema_version=1,
        status_code=201,
        body={"event_id": str(command.command_id)},
        first_book_position=appended.positions.start,
        last_book_position=appended.positions.stop - 1,
    )


def _uow_factory(runtime_url: str):
    factory = sessionmaker(create_engine(runtime_url, pool_pre_ping=True))
    return lambda: SqlAlchemyUnitOfWork(factory)


def _execute_worker(runtime_url: str, book_id_text: str, start, results) -> None:
    try:
        start.wait(timeout=30)
        outcome = execute(
            _command(UUID(book_id_text)),
            raw_key=RAW_KEY,
            actor=ACTOR,
            authorize=_authorize,
            handler=_atomic_handler,
            uow_factory=_uow_factory(runtime_url),
        )
        results.put(("ok", outcome.replayed))
    except BaseException:
        results.put(("error", traceback.format_exc()))


def _killable_worker(runtime_url: str, book_id_text: str, phase: str, reached) -> None:
    command = _command(UUID(book_id_text))

    def pausing_handler(command: FakeCommand, uow):
        if phase == "after-reservation":
            reached.put(os.getpid())
            signal.pause()
        result = _atomic_handler(command, uow)
        reached.put(os.getpid())
        signal.pause()
        return result

    execute(
        command,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=pausing_handler,
        uow_factory=_uow_factory(runtime_url),
    )


def _seed(runtime_url: str) -> UUID:
    book_id = uuid5(NAMESPACE_URL, f"receipt-book:{runtime_url}")
    engine = create_engine(runtime_url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Cross-process receipt', null, 'active')
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            insert into book_event_heads (book_id, last_position, last_hash)
            values (:book_id, 0, :zero_hash)
            """),
            {"book_id": book_id, "zero_hash": bytes(32)},
        )
        connection.execute(
            text("""
            insert into users (user_id, subject_type, current_display_name, status)
            values (:user_id, 'human', 'Cross Process', 'active')
            """),
            {"user_id": ACTOR.subject_id},
        )
        connection.execute(
            text("""
            insert into book_members (book_id, user_id, role, status, scopes)
            values (:book_id, :user_id, 'owner', 'active', '["ledger:write"]')
            """),
            {"book_id": book_id, "user_id": ACTOR.subject_id},
        )
    engine.dispose()
    return book_id


def _assert_one_completed(runtime_url: str, book_id: UUID) -> None:
    engine = create_engine(runtime_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )
        receipt = session.scalar(select(CommandReceiptRecord))
        assert receipt is not None and receipt.status == "completed"
        assert session.get(BookEventHeadRecord, book_id).last_position == 1
    engine.dispose()


def test_twenty_processes_same_key_and_payload_execute_once(
    migrated_postgres_database,
) -> None:
    runtime_url = migrated_postgres_database.runtime_url
    book_id = _seed(runtime_url)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_execute_worker,
            args=(runtime_url, str(book_id), start, results),
        )
        for _ in range(20)
    ]
    for process in processes:
        process.start()
    try:
        start.set()
        outcomes = [results.get(timeout=120) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes)
        assert all(outcome[0] == "ok" for outcome in outcomes), outcomes
        assert sum(not outcome[1] for outcome in outcomes) == 1
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
    _assert_one_completed(runtime_url, book_id)


def test_sigkill_after_reservation_or_event_insert_rolls_back_then_retry_executes_once(
    postgres_database_factory,
) -> None:
    context = multiprocessing.get_context("spawn")
    for phase in ("after-reservation", "after-event"):
        database = postgres_database_factory.create(purpose=phase, schema="v2")
        runtime_url = database.runtime_url
        book_id = _seed(runtime_url)
        reached = context.Queue()
        process = context.Process(
            target=_killable_worker,
            args=(runtime_url, str(book_id), phase, reached),
        )
        process.start()
        pid = reached.get(timeout=60)
        assert pid == process.pid
        os.kill(pid, signal.SIGKILL)
        process.join(timeout=30)
        assert process.exitcode == -signal.SIGKILL

        outcome = execute(
            _command(book_id),
            raw_key=RAW_KEY,
            actor=ACTOR,
            authorize=_authorize,
            handler=_atomic_handler,
            uow_factory=_uow_factory(runtime_url),
        )
        assert outcome.replayed is False
        _assert_one_completed(runtime_url, book_id)
