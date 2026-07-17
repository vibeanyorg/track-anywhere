from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    pending_posted_event,
    seed_journal_scenario,
)
import track_anywhere.application.command_bus as command_bus_module
from track_anywhere.application.event_batch import AppendBatchResult
from track_anywhere.application.command_bus import execute_financial
from track_anywhere.application.idempotency import AuthorizationScope, CommandActor
from track_anywhere.application.ledger_committer import (
    LedgerCommitter,
    LedgerWritePlan,
    LockedBookHead,
)
from track_anywhere.infrastructure.db.command_receipts import (
    CommandReceiptRepository,
)
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import JournalTransactionRecord
from track_anywhere.infrastructure.db.repositories import RowLock
from track_anywhere.infrastructure.db.repositories.auth import AuthRepository
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class PostCommand:
    book_id: UUID
    command_id: UUID
    transaction_id: UUID
    operation: str = "journal.post"

    def idempotency_payload(self) -> dict[str, object]:
        return {"transaction_id": str(self.transaction_id)}


class _TracingCommitter(LedgerCommitter):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def execute_under_book_lock(self, session, book_id):
        self._order.append("book_lock")
        return super().execute_under_book_lock(session, book_id)

    def append_and_project(self, *args, **kwargs):
        self._order.append("append_project")
        return super().append_and_project(*args, **kwargs)


class _TracingUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(self, session_factory, order: list[str]) -> None:
        super().__init__(session_factory)
        self._order = order

    def __exit__(self, exc_type, exc, traceback):
        result = super().__exit__(exc_type, exc, traceback)
        if exc_type is None:
            self._order.append("commit")
        return result


def test_post_projection_finalizer_is_private_and_noncomparable() -> None:
    scenario = JournalScenario.create()

    def first_finalizer(session, appended) -> None:
        del session, appended

    def second_finalizer(session, appended) -> None:
        del session, appended

    common = {
        "expected_stream_versions": {
            ("journal_transaction", scenario.transaction_id): 0
        },
        "events": (pending_posted_event(scenario),),
        "response_schema_version": 1,
        "status_code": 201,
        "body": {"transaction_id": str(scenario.transaction_id)},
    }

    first = LedgerWritePlan(
        **common,
        post_projection_finalizer=first_finalizer,
    )
    second = LedgerWritePlan(
        **common,
        post_projection_finalizer=second_finalizer,
    )

    assert first == second
    assert "finalizer" not in repr(first)


def test_financial_command_runs_finalizer_after_append_before_receipt_and_commit(
    monkeypatch,
) -> None:
    scenario = JournalScenario.create()
    actor = CommandActor(subject_id=scenario.actor_subject_id)
    command = PostCommand(
        book_id=scenario.book_id,
        command_id=scenario.command_id,
        transaction_id=scenario.transaction_id,
    )
    order: list[str] = []

    class FakeReservation:
        created = True

    class FakeReceipts:
        def __init__(self, session) -> None:
            del session

        def reserve_or_lock(self, *args, **kwargs):
            del args, kwargs
            order.append("receipt_reserve")
            return FakeReservation()

        def complete(self, *args, **kwargs):
            del args, kwargs
            order.append("receipt_complete")

    class FakeUnitOfWork:
        session = object()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc, traceback
            order.append("commit" if exc_type is None else "rollback")

    class FakeCommitter(LedgerCommitter):
        def __init__(self) -> None:
            pass

        def execute_under_book_lock(self, session, book_id):
            del session
            order.append("book_lock")
            return LockedBookHead(
                book_id=book_id,
                last_position=0,
                last_hash=bytes(32),
                _lock_token=uuid4(),
            )

        def append_and_project(self, session, **kwargs):
            del session
            order.append("append_project")
            events = kwargs["events"]
            return AppendBatchResult(
                positions=range(1, len(events) + 1),
                terminal_hash=b"t" * 32,
                event_ids=tuple(event.event_id for event in events),
            )

    monkeypatch.setattr(
        command_bus_module,
        "CommandReceiptRepository",
        FakeReceipts,
    )

    def authorize(session, actor, book_id, *, lock_membership):
        del session, lock_membership
        order.append("authorize")
        return AuthorizationScope(
            book_id=book_id,
            actor_subject_id=actor.subject_id,
            role="owner",
            scopes=("ledger:write",),
        )

    def finalizer(session, appended) -> None:
        assert session is FakeUnitOfWork.session
        assert appended.positions == range(1, 2)
        order.append("finalizer")

    def handler(command, uow, locked_head):
        del command, uow, locked_head
        order.append("handler")
        return LedgerWritePlan(
            expected_stream_versions={
                ("journal_transaction", scenario.transaction_id): 0
            },
            events=(pending_posted_event(scenario),),
            response_schema_version=1,
            status_code=201,
            body={"transaction_id": str(scenario.transaction_id)},
            post_projection_finalizer=finalizer,
        )

    execute_financial(
        command,
        raw_key="financial-command-key",
        actor=actor,
        authorize=authorize,
        handler=handler,
        uow_factory=FakeUnitOfWork,
        ledger_committer=FakeCommitter(),
    )

    assert order == [
        "authorize",
        "receipt_reserve",
        "book_lock",
        "handler",
        "append_project",
        "finalizer",
        "receipt_complete",
        "commit",
    ]


def test_financial_command_holds_book_lock_from_handler_through_receipt_completion(
    pg_engine, monkeypatch
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    actor = CommandActor(subject_id=scenario.actor_subject_id)
    command = PostCommand(
        book_id=scenario.book_id,
        command_id=scenario.command_id,
        transaction_id=scenario.transaction_id,
    )
    order: list[str] = []
    original_reserve = CommandReceiptRepository.reserve_or_lock
    original_complete = CommandReceiptRepository.complete

    def traced_reserve(self, *args, **kwargs):
        order.append("receipt_reserve")
        return original_reserve(self, *args, **kwargs)

    def traced_complete(self, *args, **kwargs):
        order.append("receipt_complete")
        return original_complete(self, *args, **kwargs)

    monkeypatch.setattr(CommandReceiptRepository, "reserve_or_lock", traced_reserve)
    monkeypatch.setattr(CommandReceiptRepository, "complete", traced_complete)

    def authorize(session, actor, book_id, *, lock_membership):
        order.append("authorize")
        membership = AuthRepository(session).get_membership(
            book_id,
            actor.subject_id,
            lock=RowLock.SHARE if lock_membership else RowLock.NONE,
        )
        return AuthorizationScope(
            book_id=book_id,
            actor_subject_id=actor.subject_id,
            role=membership.role,
            scopes=membership.scopes,
        )

    def handler(command, uow, locked_head):
        order.append("handler")
        assert locked_head.book_id == scenario.book_id
        competitor = Session(pg_engine)
        try:
            competitor.execute(text("set local lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as error_info:
                competitor.execute(
                    select(BookEventHeadRecord)
                    .where(BookEventHeadRecord.book_id == scenario.book_id)
                    .with_for_update()
                ).scalar_one()
            assert getattr(error_info.value.orig, "sqlstate", "") == "55P03"
        finally:
            competitor.rollback()
            competitor.close()
        return LedgerWritePlan(
            expected_stream_versions={
                ("journal_transaction", scenario.transaction_id): 0
            },
            events=(pending_posted_event(scenario),),
            response_schema_version=1,
            status_code=201,
            body={"transaction_id": str(scenario.transaction_id)},
        )

    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    outcome = execute_financial(
        command,
        raw_key="financial-command-key",
        actor=actor,
        authorize=authorize,
        handler=handler,
        uow_factory=lambda: _TracingUnitOfWork(session_factory, order),
        ledger_committer=_TracingCommitter(order),
    )

    assert outcome.replayed is False
    assert order == [
        "authorize",
        "receipt_reserve",
        "book_lock",
        "handler",
        "append_project",
        "receipt_complete",
        "commit",
    ]
    with Session(pg_engine) as session:
        receipt = session.scalar(select(CommandReceiptRecord))
        assert receipt is not None and receipt.status == "completed"
        assert (receipt.first_book_position, receipt.last_book_position) == (1, 1)
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )


def test_finalizer_exception_rolls_back_append_projection_and_receipt(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    actor = CommandActor(subject_id=scenario.actor_subject_id)
    command = PostCommand(
        book_id=scenario.book_id,
        command_id=scenario.command_id,
        transaction_id=scenario.transaction_id,
    )

    def authorize(session, actor, book_id, *, lock_membership):
        membership = AuthRepository(session).get_membership(
            book_id,
            actor.subject_id,
            lock=RowLock.SHARE if lock_membership else RowLock.NONE,
        )
        return AuthorizationScope(
            book_id=book_id,
            actor_subject_id=actor.subject_id,
            role=membership.role,
            scopes=membership.scopes,
        )

    def fail_finalizer(session, appended) -> None:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(JournalTransactionRecord))
            == 1
        )
        assert appended.positions == range(1, 2)
        raise RuntimeError("injected finalizer failure")

    def handler(command, uow, locked_head):
        del command, uow, locked_head
        return LedgerWritePlan(
            expected_stream_versions={
                ("journal_transaction", scenario.transaction_id): 0
            },
            events=(pending_posted_event(scenario),),
            response_schema_version=1,
            status_code=201,
            body={"transaction_id": str(scenario.transaction_id)},
            post_projection_finalizer=fail_finalizer,
        )

    session_factory = sessionmaker(pg_engine, expire_on_commit=False)
    with pytest.raises(RuntimeError, match="injected finalizer failure"):
        execute_financial(
            command,
            raw_key="financial-command-key",
            actor=actor,
            authorize=authorize,
            handler=handler,
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            ledger_committer=LedgerCommitter(),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None
        assert (head.last_position, head.last_hash) == (0, bytes(32))
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(JournalTransactionRecord))
            == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 0
        )
