from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.event_batch import PendingEvent
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
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.investments import AllocationMethod
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.investments import (
    InvestmentLotAllocationRecord,
    InvestmentLotRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


EFFECTIVE_AT = datetime(2026, 7, 14, 22, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _seed_cny(engine) -> None:
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


def _post(engine, scenario, transaction_id, command_id, minute) -> None:
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
            effective_at=EFFECTIVE_AT + timedelta(minutes=minute),
        ),
        raw_key=f"replay-journal:{command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _state(session: Session):
    return {
        "lots": tuple(
            (
                row.lot_id,
                row.acquisition_transaction_id,
                int(row.acquired_quantity_units),
                int(row.acquired_cost_units),
                int(row.remaining_quantity_units),
                int(row.remaining_cost_units),
                row.source_event_id,
                row.source_position,
            )
            for row in session.scalars(
                select(InvestmentLotRecord).order_by(InvestmentLotRecord.lot_id)
            )
        ),
        "allocations": tuple(
            (
                row.allocation_id,
                row.lot_id,
                row.disposal_transaction_id,
                row.allocation_position,
                int(row.quantity_units),
                int(row.cost_units),
                row.source_event_id,
                row.source_position,
            )
            for row in session.scalars(
                select(InvestmentLotAllocationRecord).order_by(
                    InvestmentLotAllocationRecord.allocation_position
                )
            )
        ),
    }


def test_cold_replay_uses_frozen_allocations_instead_of_rerunning_fifo(
    migrated_postgres_source_target,
) -> None:
    source_db, target_db = migrated_postgres_source_target
    source = create_engine(source_db.runtime_url, pool_pre_ping=True)
    target = create_engine(target_db.runtime_url, pool_pre_ping=True)
    scenario = JournalScenario.create()
    acquisition_transaction_id = uuid4()
    disposal_transaction_id = uuid4()
    acquisition_command_id = uuid4()
    disposal_journal_command_id = uuid4()
    lot_command_id = uuid4()
    disposal_command_id = uuid4()
    lot_id = uuid4()
    actor = CommandActor(subject_id=scenario.actor_subject_id)
    try:
        for engine in (source, target):
            seed_journal_scenario(engine, scenario)
            _seed_cny(engine)

        _post(
            source,
            scenario,
            acquisition_transaction_id,
            acquisition_command_id,
            0,
        )
        execute_acquire_lot(
            AcquireLotCommand(
                book_id=scenario.book_id,
                command_id=lot_command_id,
                transaction_id=acquisition_transaction_id,
                lot_id=lot_id,
                instrument_asset_code="USD",
                settlement_asset_code="CNY",
                quantity_units="100",
                cost_units="701",
                fee_units="3",
                effective_at=EFFECTIVE_AT + timedelta(minutes=1),
            ),
            raw_key="replay-lot-acquire",
            actor=actor,
            uow_factory=_uow_factory(source),
        )
        _post(
            source,
            scenario,
            disposal_transaction_id,
            disposal_journal_command_id,
            2,
        )
        execute_dispose_lot(
            DisposeLotCommand(
                book_id=scenario.book_id,
                command_id=disposal_command_id,
                transaction_id=disposal_transaction_id,
                instrument_asset_code="USD",
                settlement_asset_code="CNY",
                quantity_units="37",
                proceeds_units="400",
                fee_units="2",
                allocation_method=AllocationMethod.FIFO,
                effective_at=EFFECTIVE_AT + timedelta(minutes=3),
            ),
            raw_key="replay-lot-dispose",
            actor=actor,
            uow_factory=_uow_factory(source),
        )

        with Session(source) as session:
            stored = tuple(
                session.scalars(
                    select(LedgerEventRecord).order_by(LedgerEventRecord.book_position)
                )
            )
            frozen_cost = stored[-1].payload["allocations"][0]["cost_units"]
            assert frozen_cost == "259"
        pending = tuple(
            PendingEvent(
                event_id=event.event_id,
                stream_type=event.stream_type,
                stream_id=event.stream_id,
                payload=PRODUCTION_EVENT_REGISTRY.validate_stored(
                    event.event_type,
                    event.event_schema_version,
                    event.payload,
                ),
                command_id=event.command_id,
                actor_subject_id=event.actor_subject_id,
                correlation_id=event.correlation_id,
                causation_event_id=event.causation_event_id,
                effective_at=event.effective_at,
            )
            for event in stored
        )
        with Session(target) as session, session.begin():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(session, scenario.book_id)
            committer.append_and_project(
                session,
                locked_head=locked,
                expected_stream_versions={
                    ("journal_transaction", acquisition_transaction_id): 0,
                    ("investment_lot", lot_id): 0,
                    ("journal_transaction", disposal_transaction_id): 0,
                    ("investment_disposal", disposal_transaction_id): 0,
                },
                events=pending,
            )

        with Session(source) as source_session, Session(target) as target_session:
            assert _state(source_session) == _state(target_session)
    finally:
        target.dispose()
        source.dispose()
