from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.domain.journal.events import ReversalReasonCode
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.queries.balances import get_book_balances
from track_anywhere.queries.journal import list_journal


ACTOR = CommandActor(subject_id="human:journal-query")
BASE_TIME = datetime(2026, 1, 2, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _post(engine, scenario: JournalScenario, *, when: datetime, amount: str):
    transaction_id = uuid4()
    outcome = execute_post_transaction(
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
                    amount=amount,
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount=amount,
                ),
            ),
            effective_at=when,
        ),
        raw_key=f"query-post:{transaction_id}",
        actor=ACTOR,
        uow_factory=_uow_factory(engine),
    )
    return transaction_id, outcome.result.last_book_position


def _seed(engine):
    base = JournalScenario.create()
    scenario = JournalScenario(
        book_id=base.book_id,
        debit_account_id=base.debit_account_id,
        credit_account_id=base.credit_account_id,
        transaction_id=base.transaction_id,
        event_id=base.event_id,
        command_id=base.command_id,
        debit_posting_id=base.debit_posting_id,
        credit_posting_id=base.credit_posting_id,
        actor_subject_id=ACTOR.subject_id,
    )
    seed_journal_scenario(engine, scenario)
    return scenario


def test_journal_cursor_is_stable_and_honors_as_of_position(pg_engine) -> None:
    scenario = _seed(pg_engine)
    late_id, late_position = _post(
        pg_engine, scenario, when=BASE_TIME + timedelta(days=2), amount="3.00"
    )
    early_id, early_position = _post(pg_engine, scenario, when=BASE_TIME, amount="1.00")
    middle_id, middle_position = _post(
        pg_engine, scenario, when=BASE_TIME + timedelta(days=1), amount="2.00"
    )

    with Session(pg_engine) as session:
        first = list_journal(session, scenario.book_id, limit=2)
        second = list_journal(
            session, scenario.book_id, limit=2, cursor=first.next_cursor
        )
        historical = list_journal(
            session,
            scenario.book_id,
            limit=10,
            as_of_book_position=early_position,
        )

    assert [item.transaction_id for item in first.items] == [early_id, middle_id]
    assert [item.transaction_id for item in second.items] == [late_id]
    assert first.next_cursor is not None and second.next_cursor is None
    # as-of filters by immutable Book position, then keeps the public effective-time order.
    assert [item.transaction_id for item in historical.items] == [early_id, late_id]
    assert historical.as_of_book_position == early_position
    assert late_position == 1 and middle_position == 3


def test_balance_query_matches_posting_reference_and_survives_account_close(
    pg_engine,
) -> None:
    scenario = _seed(pg_engine)
    original_id, original_position = _post(
        pg_engine, scenario, when=BASE_TIME, amount="12.34"
    )
    reversal_id = uuid4()
    outcome = execute_reverse_transaction(
        ReverseTransactionCommand(
            book_id=scenario.book_id,
            command_id=uuid4(),
            reversal_transaction_id=reversal_id,
            reverses_transaction_id=original_id,
            expected_stream_version=0,
            reason_code=ReversalReasonCode.USER_CORRECTION,
            effective_at=BASE_TIME + timedelta(days=1),
        ),
        raw_key="query-reversal",
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update accounts set status='closed' "
                "where book_id=:book_id and account_id=:account_id"
            ),
            {"book_id": scenario.book_id, "account_id": scenario.debit_account_id},
        )

    with Session(pg_engine) as session:
        current = get_book_balances(session, scenario.book_id)
        historical = get_book_balances(
            session, scenario.book_id, as_of_book_position=original_position
        )
        journal = list_journal(session, scenario.book_id, limit=10)

    assert {(row.account_id, row.units) for row in current.items} == {
        (scenario.debit_account_id, 0),
        (scenario.credit_account_id, 0),
    }
    assert current.projection_matches_reference is True
    assert {(row.account_id, row.units) for row in historical.items} == {
        (scenario.debit_account_id, 1234),
        (scenario.credit_account_id, -1234),
    }
    by_id = {item.transaction_id: item for item in journal.items}
    assert by_id[original_id].reversed_by_transaction_id == reversal_id
    assert by_id[reversal_id].reverses_transaction_id == original_id
    assert outcome.result.last_book_position == 2
