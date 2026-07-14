from __future__ import annotations

from datetime import UTC, datetime, timedelta
import multiprocessing
import traceback
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.investments import (
    AcquireLotCommand,
    DisposeLotCommand,
    execute_acquire_lot,
    execute_dispose_lot,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.domain.investments import AllocationMethod, OverDisposal
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.investments import (
    InvestmentLotAllocationRecord,
    InvestmentLotRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


ACTOR = CommandActor(subject_id="human:concurrent-lot")
EFFECTIVE_AT = datetime(2026, 7, 14, 23, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _linked_transaction_id(book_id: UUID, index: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"lot-disposal-transaction:{book_id}:{index}")


def _worker(runtime_url: str, book_id: str, index: int, start, results) -> None:
    engine = create_engine(runtime_url, pool_pre_ping=True)
    try:
        if not start.wait(timeout=30):
            raise RuntimeError("lot disposal start gate timed out")
        parsed_book_id = UUID(book_id)
        command_id = uuid5(
            NAMESPACE_URL,
            f"lot-disposal-command:{parsed_book_id}:{index}",
        )
        execute_dispose_lot(
            DisposeLotCommand(
                book_id=parsed_book_id,
                command_id=command_id,
                transaction_id=_linked_transaction_id(parsed_book_id, index),
                instrument_asset_code="USD",
                settlement_asset_code="CNY",
                quantity_units="1",
                proceeds_units="11",
                fee_units="1",
                allocation_method=AllocationMethod.FIFO,
                effective_at=EFFECTIVE_AT + timedelta(microseconds=index),
            ),
            raw_key=f"concurrent-lot-disposal:{index}",
            actor=ACTOR,
            uow_factory=_uow_factory(engine),
        )
        results.put(("ok", index))
    except OverDisposal:
        results.put(("over_disposal", index))
    except BaseException:
        results.put(("error", traceback.format_exc()))
    finally:
        engine.dispose()


def _post_linked(engine, scenario, transaction_id, command_id, minute) -> None:
    execute_post_transaction(
        PostTransactionCommand(
            book_id=scenario.book_id,
            command_id=command_id,
            transaction_id=transaction_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=(
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.debit_account_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount="0.01",
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="0.01",
                ),
            ),
            effective_at=EFFECTIVE_AT + timedelta(minutes=minute),
        ),
        raw_key=f"lot-linked:{command_id}",
        actor=ACTOR,
        uow_factory=_uow_factory(engine),
    )


def test_twenty_processes_cannot_double_allocate_a_limited_lot(
    migrated_postgres_database,
) -> None:
    runtime_url = migrated_postgres_database.runtime_url
    engine = create_engine(runtime_url, pool_pre_ping=True)
    original = JournalScenario.create()
    scenario = JournalScenario(
        book_id=original.book_id,
        debit_account_id=original.debit_account_id,
        credit_account_id=original.credit_account_id,
        transaction_id=original.transaction_id,
        event_id=original.event_id,
        command_id=original.command_id,
        debit_posting_id=original.debit_posting_id,
        credit_posting_id=original.credit_posting_id,
        actor_subject_id=ACTOR.subject_id,
    )
    seed_journal_scenario(engine, scenario)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into assets ("
                "asset_code, kind, ledger_scale, input_scale, display_scale, "
                "current_name, status"
                ") values ('CNY', 'fiat', 2, 2, 2, 'Chinese Yuan', 'active') "
                "on conflict (asset_code) do nothing"
            )
        )

    acquisition_transaction_id = uuid5(
        NAMESPACE_URL,
        f"lot-acquisition-transaction:{scenario.book_id}",
    )
    _post_linked(
        engine,
        scenario,
        acquisition_transaction_id,
        uuid5(NAMESPACE_URL, f"lot-acquisition-journal:{scenario.book_id}"),
        0,
    )
    lot_id = uuid5(NAMESPACE_URL, f"limited-lot:{scenario.book_id}")
    execute_acquire_lot(
        AcquireLotCommand(
            book_id=scenario.book_id,
            command_id=uuid5(
                NAMESPACE_URL,
                f"lot-acquisition-command:{scenario.book_id}",
            ),
            transaction_id=acquisition_transaction_id,
            lot_id=lot_id,
            instrument_asset_code="USD",
            settlement_asset_code="CNY",
            quantity_units="10",
            cost_units="100",
            effective_at=EFFECTIVE_AT,
        ),
        raw_key="limited-lot-acquire",
        actor=ACTOR,
        uow_factory=_uow_factory(engine),
    )
    for index in range(20):
        _post_linked(
            engine,
            scenario,
            _linked_transaction_id(scenario.book_id, index),
            uuid5(
                NAMESPACE_URL,
                f"lot-disposal-journal-command:{scenario.book_id}:{index}",
            ),
            index + 1,
        )

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_worker,
            args=(runtime_url, str(scenario.book_id), index, start, results),
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
        assert sum(outcome[0] == "ok" for outcome in outcomes) == 10, outcomes
        assert sum(outcome[0] == "over_disposal" for outcome in outcomes) == 10, (
            outcomes
        )
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    with Session(engine) as session:
        lot = session.get(InvestmentLotRecord, (scenario.book_id, lot_id))
        assert lot is not None
        assert int(lot.remaining_quantity_units) == 0
        assert int(lot.remaining_cost_units) == 0
        allocations = tuple(session.scalars(select(InvestmentLotAllocationRecord)))
        assert len(allocations) == 10
        assert len({allocation.allocation_id for allocation in allocations}) == 10
        assert sum(int(allocation.quantity_units) for allocation in allocations) == 10
        assert sum(int(allocation.cost_units) for allocation in allocations) == 100
        assert (
            session.scalar(
                select(func.count())
                .select_from(LedgerEventRecord)
                .where(LedgerEventRecord.event_type == "InvestmentLotDisposed")
            )
            == 10
        )
    engine.dispose()
