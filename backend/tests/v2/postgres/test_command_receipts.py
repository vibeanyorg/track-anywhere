from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.application.command_bus import execute
from track_anywhere.application.idempotency import (
    AuthorizationScope,
    CommandActor,
    CommandResult,
    IdempotencyConflict,
)
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.domain.investments.events import InvestmentLotAcquired
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    SynchronousProjectionAppliedEventRecord,
)
from track_anywhere.infrastructure.db.repositories import RowLock
from track_anywhere.infrastructure.db.repositories.auth import (
    AuthRepository,
    BookMembershipSnapshot,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


ACTOR = CommandActor(subject_id="human:receipt-test")
RAW_KEY = "raw-key-that-must-never-be-stored"
EFFECTIVE_AT = datetime(2026, 7, 14, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeCommand:
    book_id: UUID
    command_id: UUID
    stream_id: UUID
    amount: str
    operation: str = "fake.atomic-append"

    def idempotency_payload(self) -> dict[str, object]:
        return {"amount": self.amount, "stream_id": str(self.stream_id)}


def _seed_authorized_book(pg_engine) -> UUID:
    book_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Receipt protocol', null, 'active')
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
            values (:user_id, 'human', 'Receipt Test', 'active')
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
    return book_id


def _authorize(session, actor, book_id, *, lock_membership):
    assert lock_membership is True
    lock = RowLock.SHARE if lock_membership else RowLock.NONE
    membership: BookMembershipSnapshot = AuthRepository(session).get_membership(
        book_id, actor.subject_id, lock=lock
    )
    if membership.status != "active" or "ledger:write" not in membership.scopes:
        raise PermissionError("current membership does not authorize this command")
    return AuthorizationScope(
        book_id=book_id,
        actor_subject_id=actor.subject_id,
        role=membership.role,
        scopes=membership.scopes,
    )


def _handler(command: FakeCommand, uow) -> CommandResult:
    event = PendingEvent(
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
        expected_stream_versions={
            ("investment_lot", command.stream_id): 0,
        },
        events=(event,),
    )
    # Infrastructure-only receipt test: acknowledge the now-sync-required lot
    # event without exercising the production lot projection coordinator.
    uow.session.add(
        SynchronousProjectionAppliedEventRecord(
            book_id=command.book_id,
            event_id=event.event_id,
            projection_version=1,
        )
    )
    return CommandResult(
        response_schema_version=2,
        status_code=201,
        body={"event_id": str(command.command_id), "amount": command.amount},
        first_book_position=appended.positions.start,
        last_book_position=appended.positions.stop - 1,
    )


def _uow_factory(pg_engine):
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


class _InjectedDatabaseFailure(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        super().__init__("injected database failure")


class _CommitThenDisconnectUnitOfWork:
    def __init__(self, inner, disconnect_once: dict[str, bool]) -> None:
        self._inner = inner
        self._disconnect_once = disconnect_once

    def __enter__(self):
        self._inner.__enter__()
        self.session = self._inner.session
        return self

    def __exit__(self, exc_type, exc, traceback):
        result = self._inner.__exit__(exc_type, exc, traceback)
        if exc_type is None and self._disconnect_once["pending"]:
            self._disconnect_once["pending"] = False
            raise OperationalError(
                "commit",
                {},
                _InjectedDatabaseFailure("08006"),
                connection_invalidated=True,
            )
        return result


def test_execute_commits_effect_and_versioned_receipt_then_stably_replays(
    pg_engine, caplog
) -> None:
    book_id = _seed_authorized_book(pg_engine)
    command = FakeCommand(book_id, uuid4(), uuid4(), "1000")

    first = execute(
        command,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=_handler,
        uow_factory=_uow_factory(pg_engine),
    )
    retry = FakeCommand(book_id, uuid4(), command.stream_id, "1000")
    replay = execute(
        retry,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=lambda *_: pytest.fail("replay executed the handler"),
        uow_factory=_uow_factory(pg_engine),
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.result == first.result
    assert replay.result.response_schema_version == 2
    with Session(pg_engine) as session:
        receipt = session.scalar(select(CommandReceiptRecord))
        assert receipt is not None
        assert receipt.status == "completed"
        assert receipt.idempotency_key_hash != RAW_KEY.encode()
        assert receipt.result_body == first.result.body
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert session.get(BookEventHeadRecord, book_id).last_position == 1
        database_text = session.scalar(
            text(
                "select string_agg(value, ' ') from (select command_id::text as value from command_receipts union all select encode(idempotency_key_hash, 'hex') from command_receipts union all select request_hash::text from command_receipts union all select result_body::text from command_receipts) values"
            )
        )
    assert RAW_KEY not in (database_text or "")
    assert RAW_KEY not in caplog.text


def test_same_key_different_payload_is_typed_conflict_and_does_not_append(
    pg_engine,
) -> None:
    book_id = _seed_authorized_book(pg_engine)
    original = FakeCommand(book_id, uuid4(), uuid4(), "1000")
    execute(
        original,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=_handler,
        uow_factory=_uow_factory(pg_engine),
    )

    with pytest.raises(IdempotencyConflict) as error_info:
        execute(
            FakeCommand(book_id, uuid4(), original.stream_id, "999"),
            raw_key=RAW_KEY,
            actor=ACTOR,
            authorize=_authorize,
            handler=_handler,
            uow_factory=_uow_factory(pg_engine),
        )

    assert error_info.value.status_code == 409
    assert RAW_KEY not in str(error_info.value)
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )


def test_handler_exception_rolls_back_reservation_and_atomic_effect(pg_engine) -> None:
    book_id = _seed_authorized_book(pg_engine)
    command = FakeCommand(book_id, uuid4(), uuid4(), "1000")

    def failing_handler(command: FakeCommand, uow):
        _handler(command, uow)
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        execute(
            command,
            raw_key=RAW_KEY,
            actor=ACTOR,
            authorize=_authorize,
            handler=failing_handler,
            uow_factory=_uow_factory(pg_engine),
        )

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )
        assert session.get(BookEventHeadRecord, book_id).last_position == 0


def test_current_authorization_is_checked_before_completed_receipt_replay(
    pg_engine,
) -> None:
    book_id = _seed_authorized_book(pg_engine)
    command = FakeCommand(book_id, uuid4(), uuid4(), "1000")
    execute(
        command,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=_handler,
        uow_factory=_uow_factory(pg_engine),
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            update book_members
               set status = 'revoked', revoked_at = clock_timestamp()
             where book_id = :book_id and user_id = :user_id
            """),
            {"book_id": book_id, "user_id": ACTOR.subject_id},
        )

    with pytest.raises(PermissionError, match="current membership"):
        execute(
            FakeCommand(book_id, uuid4(), command.stream_id, "1000"),
            raw_key=RAW_KEY,
            actor=ACTOR,
            authorize=_authorize,
            handler=lambda *_: pytest.fail("denied replay ran handler"),
            uow_factory=_uow_factory(pg_engine),
        )


def test_authorization_scope_is_bound_into_request_hash(pg_engine) -> None:
    book_id = _seed_authorized_book(pg_engine)
    command = FakeCommand(book_id, uuid4(), uuid4(), "1000")
    execute(
        command,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=_handler,
        uow_factory=_uow_factory(pg_engine),
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            update book_members set role = 'admin'
             where book_id = :book_id and user_id = :user_id
            """),
            {"book_id": book_id, "user_id": ACTOR.subject_id},
        )

    with pytest.raises(IdempotencyConflict):
        execute(
            FakeCommand(book_id, uuid4(), command.stream_id, "1000"),
            raw_key=RAW_KEY,
            actor=ACTOR,
            authorize=_authorize,
            handler=_handler,
            uow_factory=_uow_factory(pg_engine),
        )


def test_deadlock_retries_the_entire_uow_without_duplicate_effect(pg_engine) -> None:
    book_id = _seed_authorized_book(pg_engine)
    command = FakeCommand(book_id, uuid4(), uuid4(), "1000")
    handler_calls = 0

    def deadlock_once(command: FakeCommand, uow) -> CommandResult:
        nonlocal handler_calls
        handler_calls += 1
        result = _handler(command, uow)
        if handler_calls == 1:
            raise OperationalError(
                "append",
                {},
                _InjectedDatabaseFailure("40P01"),
            )
        return result

    outcome = execute(
        command,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=_authorize,
        handler=deadlock_once,
        uow_factory=_uow_factory(pg_engine),
    )

    assert handler_calls == 2
    assert outcome.replayed is False
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )
        assert session.get(BookEventHeadRecord, book_id).last_position == 1


def test_commit_outcome_unknown_reauthorizes_and_replays_without_rerunning_handler(
    pg_engine,
) -> None:
    book_id = _seed_authorized_book(pg_engine)
    command = FakeCommand(book_id, uuid4(), uuid4(), "1000")
    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    disconnect_once = {"pending": True}
    handler_calls = 0
    authorization_calls = 0

    def uow_factory():
        return _CommitThenDisconnectUnitOfWork(
            SqlAlchemyUnitOfWork(session_factory), disconnect_once
        )

    def counting_authorize(*args, **kwargs):
        nonlocal authorization_calls
        authorization_calls += 1
        return _authorize(*args, **kwargs)

    def counting_handler(command: FakeCommand, uow) -> CommandResult:
        nonlocal handler_calls
        handler_calls += 1
        return _handler(command, uow)

    outcome = execute(
        command,
        raw_key=RAW_KEY,
        actor=ACTOR,
        authorize=counting_authorize,
        handler=counting_handler,
        uow_factory=uow_factory,
    )

    assert disconnect_once["pending"] is False
    assert authorization_calls == 2
    assert handler_calls == 1
    assert outcome.replayed is True
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )
        assert session.get(BookEventHeadRecord, book_id).last_position == 1
