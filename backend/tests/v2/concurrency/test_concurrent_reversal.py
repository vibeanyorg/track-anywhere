from __future__ import annotations

from datetime import UTC, datetime, timedelta
import multiprocessing
import traceback
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    TransactionAlreadyReversed,
    execute_reverse_transaction,
)
from track_anywhere.domain.journal.events import ReversalReasonCode
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.models.event_store import (
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    TransactionReversalRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


ACTOR = CommandActor(subject_id="human:concurrent-reversal")
EFFECTIVE_AT = datetime(2026, 7, 14, 15, tzinfo=UTC)


def _uow_factory(runtime_url: str):
    engine = create_engine(runtime_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    return engine, lambda: SqlAlchemyUnitOfWork(factory)


def _worker(
    runtime_url: str, book_id: str, transaction_id: str, index: int, start, results
):
    engine, uow_factory = _uow_factory(runtime_url)
    try:
        start.wait(timeout=30)
        command = ReverseTransactionCommand(
            book_id=UUID(book_id),
            command_id=uuid5(NAMESPACE_URL, f"reverse-command:{book_id}:{index}"),
            reversal_transaction_id=uuid5(
                NAMESPACE_URL, f"reverse-transaction:{book_id}:{index}"
            ),
            reverses_transaction_id=UUID(transaction_id),
            expected_stream_version=0,
            reason_code=ReversalReasonCode.DUPLICATE,
            effective_at=EFFECTIVE_AT + timedelta(microseconds=index),
        )
        execute_reverse_transaction(
            command,
            raw_key=f"concurrent-reversal:{index}",
            actor=ACTOR,
            uow_factory=uow_factory,
        )
        results.put(("ok", index))
    except TransactionAlreadyReversed:
        results.put(("already_reversed", index))
    except BaseException:
        results.put(("error", traceback.format_exc()))
    finally:
        engine.dispose()


def test_twenty_processes_can_commit_only_one_reversal(
    migrated_postgres_database,
) -> None:
    runtime_url = migrated_postgres_database.runtime_url
    engine = create_engine(runtime_url, pool_pre_ping=True)
    scenario = JournalScenario.create()
    scenario = JournalScenario(
        book_id=scenario.book_id,
        debit_account_id=scenario.debit_account_id,
        credit_account_id=scenario.credit_account_id,
        transaction_id=scenario.transaction_id,
        event_id=scenario.event_id,
        command_id=scenario.command_id,
        debit_posting_id=scenario.debit_posting_id,
        credit_posting_id=scenario.credit_posting_id,
        actor_subject_id=ACTOR.subject_id,
    )
    seed_journal_scenario(engine, scenario)
    factory = sessionmaker(engine, expire_on_commit=False)
    execute_post_transaction(
        PostTransactionCommand(
            book_id=scenario.book_id,
            command_id=scenario.command_id,
            transaction_id=scenario.transaction_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=(
                PostTransactionPosting(
                    posting_id=scenario.debit_posting_id,
                    account_id=scenario.debit_account_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount="10.00",
                ),
                PostTransactionPosting(
                    posting_id=scenario.credit_posting_id,
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="10.00",
                ),
            ),
            effective_at=EFFECTIVE_AT,
        ),
        raw_key="original-post",
        actor=ACTOR,
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
    )

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_worker,
            args=(
                runtime_url,
                str(scenario.book_id),
                str(scenario.transaction_id),
                index,
                start,
                results,
            ),
        )
        for index in range(20)
    ]
    for process in processes:
        process.start()
    try:
        start.set()
        outcomes = [results.get(timeout=120) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes)
        assert sum(outcome[0] == "ok" for outcome in outcomes) == 1, outcomes
        assert sum(outcome[0] == "already_reversed" for outcome in outcomes) == 19
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 2
        assert (
            session.scalar(select(func.count()).select_from(TransactionReversalRecord))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 2
        )
    engine.dispose()
