from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.queries.journal import list_journal


def test_committed_write_is_immediately_visible_through_an_independent_engine(
    migrated_postgres_database,
) -> None:
    writer = create_engine(migrated_postgres_database.runtime_url, pool_pre_ping=True)
    reader = create_engine(migrated_postgres_database.runtime_url, pool_pre_ping=True)
    base = JournalScenario.create()
    actor = CommandActor(subject_id="human:cross-worker-read")
    scenario = JournalScenario(
        book_id=base.book_id,
        debit_account_id=base.debit_account_id,
        credit_account_id=base.credit_account_id,
        transaction_id=base.transaction_id,
        event_id=base.event_id,
        command_id=base.command_id,
        debit_posting_id=base.debit_posting_id,
        credit_posting_id=base.credit_posting_id,
        actor_subject_id=actor.subject_id,
    )
    seed_journal_scenario(writer, scenario)
    factory = sessionmaker(writer, expire_on_commit=False)
    transaction_id = uuid4()
    execute_post_transaction(
        PostTransactionCommand(
            book_id=scenario.book_id,
            command_id=uuid4(),
            transaction_id=transaction_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=(
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.debit_account_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount="5.00",
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="5.00",
                ),
            ),
            effective_at=datetime(2026, 7, 14, tzinfo=UTC),
        ),
        raw_key="cross-worker-read",
        actor=actor,
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
    )

    with Session(reader) as session:
        page = list_journal(session, scenario.book_id, limit=10)
    assert [item.transaction_id for item in page.items] == [transaction_id]
    writer.dispose()
    reader.dispose()
