from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.event_batch import PendingEvent
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
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.investments import (
    AllocationMethod,
    OverDisposal,
    SpecificLotRequest,
)
from track_anywhere.domain.investments.events import (
    InvestmentLotDisposed,
    LotDisposalAllocation,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.investments import (
    InvestmentLotAllocationRecord,
    InvestmentLotRecord,
)
from track_anywhere.infrastructure.db.models.projections import AccountBalanceRecord
from track_anywhere.infrastructure.db.models.projections import (
    SynchronousProjectionAppliedEventRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


EFFECTIVE_AT = datetime(2026, 7, 14, 20, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _seed_settlement_asset(engine) -> None:
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


def _post_linked_transaction(
    engine,
    scenario: JournalScenario,
    *,
    transaction_id: UUID,
    command_id: UUID,
    effective_at: datetime,
) -> None:
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
                    amount="1.00",
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="1.00",
                ),
            ),
            effective_at=effective_at,
        ),
        raw_key=f"linked:{command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _acquire(
    engine,
    scenario: JournalScenario,
    *,
    transaction_id: UUID,
    lot_id: UUID,
    quantity: str = "100",
    cost: str = "700",
    effective_at: datetime = EFFECTIVE_AT + timedelta(minutes=1),
):
    command = AcquireLotCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=transaction_id,
        lot_id=lot_id,
        instrument_asset_code="USD",
        settlement_asset_code="CNY",
        quantity_units=quantity,
        cost_units=cost,
        fee_units="10",
        effective_at=effective_at,
    )
    return execute_acquire_lot(
        command,
        raw_key=f"acquire:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _dispose(
    engine,
    scenario: JournalScenario,
    *,
    transaction_id: UUID,
    quantity: str,
    method: AllocationMethod = AllocationMethod.FIFO,
    specific_lots: tuple[SpecificLotRequest, ...] = (),
):
    command = DisposeLotCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=transaction_id,
        instrument_asset_code="USD",
        settlement_asset_code="CNY",
        quantity_units=quantity,
        proceeds_units="600",
        fee_units="2",
        allocation_method=method,
        specific_lots=specific_lots,
        effective_at=EFFECTIVE_AT + timedelta(minutes=3),
    )
    return execute_dispose_lot(
        command,
        raw_key=f"dispose:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _balances(session: Session):
    return tuple(
        sorted(
            (
                row.account_id,
                row.asset_code,
                int(row.balance_units),
                row.as_of_position,
            )
            for row in session.scalars(select(AccountBalanceRecord))
        )
    )


def test_acquire_and_fifo_dispose_project_frozen_allocations_without_balance_mutation(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _seed_settlement_asset(pg_engine)
    acquisition_transaction_id = uuid4()
    disposal_transaction_id = uuid4()
    lot_id = uuid4()
    _post_linked_transaction(
        pg_engine,
        scenario,
        transaction_id=acquisition_transaction_id,
        command_id=uuid4(),
        effective_at=EFFECTIVE_AT,
    )
    with Session(pg_engine) as session:
        before_acquire = _balances(session)

    acquired = _acquire(
        pg_engine,
        scenario,
        transaction_id=acquisition_transaction_id,
        lot_id=lot_id,
    )
    with Session(pg_engine) as session:
        assert _balances(session) == before_acquire
    _post_linked_transaction(
        pg_engine,
        scenario,
        transaction_id=disposal_transaction_id,
        command_id=uuid4(),
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )
    with Session(pg_engine) as session:
        before_dispose = _balances(session)

    disposed = _dispose(
        pg_engine,
        scenario,
        transaction_id=disposal_transaction_id,
        quantity="60",
    )

    assert acquired.result.body["as_of_book_position"] == 2
    assert disposed.result.body == {
        "transaction_id": str(disposal_transaction_id),
        "quantity_units": "60",
        "cost_basis_units": "420",
        "as_of_book_position": 4,
    }
    with Session(pg_engine) as session:
        lot = session.get(InvestmentLotRecord, (scenario.book_id, lot_id))
        assert lot is not None
        assert (
            int(lot.acquired_quantity_units),
            int(lot.acquired_cost_units),
            int(lot.remaining_quantity_units),
            int(lot.remaining_cost_units),
            int(lot.fee_units),
            lot.source_position,
        ) == (100, 700, 40, 280, 10, 4)
        allocation = session.scalar(select(InvestmentLotAllocationRecord))
        assert allocation is not None
        assert (
            allocation.disposal_transaction_id,
            allocation.lot_id,
            int(allocation.quantity_units),
            int(allocation.cost_units),
            allocation.source_position,
        ) == (disposal_transaction_id, lot_id, 60, 420, 4)
        assert _balances(session) == before_dispose
        disposal_event = session.scalar(
            select(LedgerEventRecord).where(
                LedgerEventRecord.event_type == "InvestmentLotDisposed"
            )
        )
        assert disposal_event is not None
        assert disposal_event.payload["allocations"][0]["cost_units"] == "420"


def test_specific_id_uses_the_requested_lot_and_preserves_fifo_independence(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _seed_settlement_asset(pg_engine)
    first_transaction, second_transaction, disposal_transaction = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    first_lot, second_lot = uuid4(), uuid4()
    for position, transaction_id in enumerate(
        (first_transaction, second_transaction, disposal_transaction)
    ):
        _post_linked_transaction(
            pg_engine,
            scenario,
            transaction_id=transaction_id,
            command_id=uuid4(),
            effective_at=EFFECTIVE_AT + timedelta(minutes=position * 2),
        )
        if position < 2:
            _acquire(
                pg_engine,
                scenario,
                transaction_id=transaction_id,
                lot_id=(first_lot, second_lot)[position],
                quantity="50",
                cost=("250", "400")[position],
                effective_at=EFFECTIVE_AT + timedelta(minutes=position * 2 + 1),
            )

    _dispose(
        pg_engine,
        scenario,
        transaction_id=disposal_transaction,
        quantity="20",
        method=AllocationMethod.SPECIFIC_ID,
        specific_lots=(SpecificLotRequest(second_lot, 20),),
    )

    with Session(pg_engine) as session:
        rows = tuple(
            session.scalars(
                select(InvestmentLotRecord).order_by(
                    InvestmentLotRecord.acquisition_transaction_id
                )
            )
        )
        by_id = {row.lot_id: row for row in rows}
        assert int(by_id[first_lot].remaining_quantity_units) == 50
        assert int(by_id[second_lot].remaining_quantity_units) == 30
        allocation = session.scalar(select(InvestmentLotAllocationRecord))
        assert allocation is not None and allocation.lot_id == second_lot


def test_over_disposal_rolls_back_event_receipt_and_projection(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _seed_settlement_asset(pg_engine)
    acquisition_transaction_id, disposal_transaction_id = uuid4(), uuid4()
    lot_id = uuid4()
    for minute, transaction_id in enumerate(
        (acquisition_transaction_id, disposal_transaction_id)
    ):
        _post_linked_transaction(
            pg_engine,
            scenario,
            transaction_id=transaction_id,
            command_id=uuid4(),
            effective_at=EFFECTIVE_AT + timedelta(minutes=minute * 2),
        )
        if minute == 0:
            _acquire(
                pg_engine,
                scenario,
                transaction_id=transaction_id,
                lot_id=lot_id,
                quantity="10",
                cost="70",
            )
    with Session(pg_engine) as session:
        events_before = session.scalar(
            select(func.count()).select_from(LedgerEventRecord)
        )
        receipts_before = session.scalar(
            select(func.count()).select_from(CommandReceiptRecord)
        )
        head_before = session.get(BookEventHeadRecord, scenario.book_id)
        assert head_before is not None
        position_before = head_before.last_position

    with pytest.raises(OverDisposal):
        _dispose(
            pg_engine,
            scenario,
            transaction_id=disposal_transaction_id,
            quantity="11",
        )

    with Session(pg_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(LedgerEventRecord))
            == events_before
        )
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord))
            == receipts_before
        )
        head = session.get(BookEventHeadRecord, scenario.book_id)
        lot = session.get(InvestmentLotRecord, (scenario.book_id, lot_id))
        assert head is not None and head.last_position == position_before
        assert lot is not None and int(lot.remaining_quantity_units) == 10


def test_projection_applies_the_frozen_cost_without_rederiving_current_fifo_math(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _seed_settlement_asset(pg_engine)
    acquisition_transaction_id, disposal_transaction_id = uuid4(), uuid4()
    lot_id = uuid4()
    for minute, transaction_id in enumerate(
        (acquisition_transaction_id, disposal_transaction_id)
    ):
        _post_linked_transaction(
            pg_engine,
            scenario,
            transaction_id=transaction_id,
            command_id=uuid4(),
            effective_at=EFFECTIVE_AT + timedelta(minutes=minute * 2),
        )
        if minute == 0:
            _acquire(
                pg_engine,
                scenario,
                transaction_id=transaction_id,
                lot_id=lot_id,
                quantity="100",
                cost="70",
            )
    command_id = uuid4()
    event_id = uuid4()
    allocation_id = uuid4()

    with Session(pg_engine) as session, session.begin():
        committer = LedgerCommitter()
        locked = committer.execute_under_book_lock(session, scenario.book_id)
        committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={
                ("investment_disposal", disposal_transaction_id): 0
            },
            events=(
                PendingEvent(
                    event_id=event_id,
                    stream_type="investment_disposal",
                    stream_id=disposal_transaction_id,
                    payload=InvestmentLotDisposed(
                        transaction_id=disposal_transaction_id,
                        instrument_asset_code="USD",
                        settlement_asset_code="CNY",
                        quantity_units="40",
                        proceeds_units="50",
                        cost_basis_units="29",
                        allocations=(
                            LotDisposalAllocation(
                                allocation_id=allocation_id,
                                lot_id=lot_id,
                                position=0,
                                quantity_units="40",
                                # Current FIFO math yields 28. The immutable event
                                # deliberately freezes 29 for replay compatibility.
                                cost_units="29",
                            ),
                        ),
                    ),
                    command_id=command_id,
                    actor_subject_id=scenario.actor_subject_id,
                    correlation_id=command_id,
                    causation_event_id=None,
                    effective_at=EFFECTIVE_AT + timedelta(minutes=3),
                ),
            ),
        )

    with Session(pg_engine) as session:
        lot = session.get(InvestmentLotRecord, (scenario.book_id, lot_id))
        allocation = session.get(
            InvestmentLotAllocationRecord,
            (scenario.book_id, allocation_id),
        )
        assert lot is not None and allocation is not None
        assert int(lot.remaining_quantity_units) == 60
        assert int(lot.remaining_cost_units) == 41
        assert int(allocation.cost_units) == 29


def test_database_rejects_lot_decrement_without_the_exact_frozen_allocation(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _seed_settlement_asset(pg_engine)
    acquisition_transaction_id, disposal_transaction_id = uuid4(), uuid4()
    lot_id = uuid4()
    for minute, transaction_id in enumerate(
        (acquisition_transaction_id, disposal_transaction_id)
    ):
        _post_linked_transaction(
            pg_engine,
            scenario,
            transaction_id=transaction_id,
            command_id=uuid4(),
            effective_at=EFFECTIVE_AT + timedelta(minutes=minute * 2),
        )
        if minute == 0:
            _acquire(
                pg_engine,
                scenario,
                transaction_id=transaction_id,
                lot_id=lot_id,
                quantity="100",
                cost="70",
            )
    command_id, event_id, allocation_id = uuid4(), uuid4(), uuid4()
    payload = InvestmentLotDisposed(
        transaction_id=disposal_transaction_id,
        instrument_asset_code="USD",
        settlement_asset_code="CNY",
        quantity_units="40",
        proceeds_units="50",
        cost_basis_units="29",
        allocations=(
            LotDisposalAllocation(
                allocation_id=allocation_id,
                lot_id=lot_id,
                position=0,
                quantity_units="40",
                cost_units="29",
            ),
        ),
    )
    with Session(pg_engine) as session, session.begin():
        result = PostgresEventStore()._append_batch(
            session,
            book_id=scenario.book_id,
            expected_stream_versions={
                ("investment_disposal", disposal_transaction_id): 0
            },
            events=(
                PendingEvent(
                    event_id=event_id,
                    stream_type="investment_disposal",
                    stream_id=disposal_transaction_id,
                    payload=payload,
                    command_id=command_id,
                    actor_subject_id=scenario.actor_subject_id,
                    correlation_id=command_id,
                    causation_event_id=None,
                    effective_at=EFFECTIVE_AT + timedelta(minutes=3),
                ),
            ),
        )
        session.add(
            SynchronousProjectionAppliedEventRecord(
                book_id=scenario.book_id,
                event_id=event_id,
                projection_version=1,
            )
        )
        source_position = result.positions.start

    with pytest.raises(IntegrityError, match="exact immutable allocation"):
        with Session(pg_engine) as session, session.begin():
            lot = session.get(InvestmentLotRecord, (scenario.book_id, lot_id))
            assert lot is not None
            lot.remaining_quantity_units = 50
            lot.remaining_cost_units = 35
            lot.source_event_id = event_id
            lot.source_position = source_position

    with pytest.raises(IntegrityError, match="exact disposal event fact"):
        with Session(pg_engine) as session, session.begin():
            session.add(
                InvestmentLotAllocationRecord(
                    book_id=scenario.book_id,
                    allocation_id=uuid4(),
                    lot_id=lot_id,
                    disposal_transaction_id=disposal_transaction_id,
                    allocation_position=0,
                    quantity_units=50,
                    cost_units=35,
                    source_event_id=event_id,
                    source_position=source_position,
                )
            )
