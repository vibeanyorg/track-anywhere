from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.credit_cards.record import (
    ChargeCreditCardCommand,
    execute_charge_credit_card,
)
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.infrastructure.db.event_store import PostgresEventStore
from track_anywhere.infrastructure.db.models.credit_cards import (
    CreditCardTransactionRecord,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.projections.synchronous import SynchronousProjector
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


EFFECTIVE_AT = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _seed_catalog(engine, scenario, *, expense_id, card_id) -> None:
    seed_journal_scenario(engine, scenario)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, current_name, status) values "
                "(:book_id, :expense_id, 'USD', 'expense', null, "
                "'Card expense', 'active'), "
                "(:book_id, :card_id, 'USD', 'liability', 'credit_card', "
                "'Credit card', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "expense_id": expense_id,
                "card_id": card_id,
            },
        )


def _projection_snapshot(session: Session, book_id):
    transaction = session.scalar(
        select(JournalTransactionRecord).where(
            JournalTransactionRecord.book_id == book_id
        )
    )
    relation = session.scalar(
        select(CreditCardTransactionRecord).where(
            CreditCardTransactionRecord.book_id == book_id
        )
    )
    postings = tuple(
        (
            row.posting_position,
            row.account_id,
            row.side,
            int(row.units),
        )
        for row in session.scalars(
            select(JournalPostingRecord)
            .where(JournalPostingRecord.book_id == book_id)
            .order_by(JournalPostingRecord.posting_position)
        )
    )
    balances = tuple(
        sorted(
            (row.account_id, int(row.balance_units), row.as_of_position)
            for row in session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == book_id
                )
            )
        )
    )
    assert transaction is not None and relation is not None
    return (
        transaction.transaction_id,
        transaction.source_position,
        transaction.transaction_kind,
        relation.intent,
        relation.card_account_id,
        relation.counter_account_id,
        int(relation.units),
        postings,
        balances,
    )


def test_cold_replay_matches_online_credit_card_projection(
    migrated_postgres_source_target,
) -> None:
    source_database, target_database = migrated_postgres_source_target
    source_engine = create_engine(source_database.runtime_url, pool_pre_ping=True)
    target_engine = create_engine(target_database.runtime_url, pool_pre_ping=True)
    scenario = JournalScenario.create()
    expense_id = uuid4()
    card_id = uuid4()
    transaction_id = uuid4()
    command_id = uuid4()
    try:
        _seed_catalog(source_engine, scenario, expense_id=expense_id, card_id=card_id)
        _seed_catalog(target_engine, scenario, expense_id=expense_id, card_id=card_id)
        source_factory = sessionmaker(source_engine, expire_on_commit=False)
        execute_charge_credit_card(
            ChargeCreditCardCommand(
                book_id=scenario.book_id,
                command_id=command_id,
                transaction_id=transaction_id,
                expected_stream_version=0,
                card_account_id=card_id,
                expense_account_id=expense_id,
                asset_code="USD",
                amount="12.34",
                effective_at=EFFECTIVE_AT,
            ),
            raw_key="credit-card-replay",
            actor=CommandActor(subject_id=scenario.actor_subject_id),
            uow_factory=lambda: SqlAlchemyUnitOfWork(source_factory),
        )
        with Session(source_engine) as session:
            stored = session.scalar(select(LedgerEventRecord))
            assert stored is not None
            payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
                stored.event_type,
                stored.event_schema_version,
                stored.payload,
            )
            pending = PendingEvent(
                event_id=stored.event_id,
                stream_type=stored.stream_type,
                stream_id=stored.stream_id,
                payload=payload,
                command_id=stored.command_id,
                actor_subject_id=stored.actor_subject_id,
                correlation_id=stored.correlation_id,
                causation_event_id=stored.causation_event_id,
                effective_at=stored.effective_at,
            )

        with Session(target_engine) as session, session.begin():
            PostgresEventStore()._append_batch(
                session,
                book_id=scenario.book_id,
                expected_stream_versions={("journal_transaction", transaction_id): 0},
                events=(pending,),
            )
            stored = session.scalar(select(LedgerEventRecord))
            assert stored is not None
            result = SynchronousProjector().apply_stored(session, stored)
            assert result.required is True and result.applied is True

        with Session(source_engine) as source, Session(target_engine) as target:
            assert _projection_snapshot(source, scenario.book_id) == (
                _projection_snapshot(target, scenario.book_id)
            )
    finally:
        target_engine.dispose()
        source_engine.dispose()
