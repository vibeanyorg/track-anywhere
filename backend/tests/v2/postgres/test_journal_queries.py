from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.credit_cards import (
    ChargeCreditCardCommand,
    PaymentCreditCardCommand,
    RefundCreditCardCommand,
    execute_charge_credit_card,
    execute_payment_credit_card,
    execute_refund_credit_card,
)
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
from track_anywhere.queries.balances import (
    get_book_balances,
    get_verified_book_balances,
)
from track_anywhere.queries.journal import list_journal


ACTOR = CommandActor(subject_id="human:journal-query")
BASE_TIME = datetime(2026, 1, 2, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _post(
    engine,
    scenario: JournalScenario,
    *,
    when: datetime,
    amount: str,
    debit_account_id=None,
    credit_account_id=None,
):
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
                    account_id=debit_account_id or scenario.debit_account_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount=amount,
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=credit_account_id or scenario.credit_account_id,
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


def _seed(
    engine,
    *,
    credit_account_type: str = "asset",
    credit_account_subtype: str | None = None,
):
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
    seed_journal_scenario(
        engine,
        scenario,
        credit_account_type=credit_account_type,
        credit_account_subtype=credit_account_subtype,
    )
    return scenario


def test_current_balance_query_uses_projection_without_reading_journal_history(
    pg_engine,
) -> None:
    scenario = _seed(pg_engine)
    _post(pg_engine, scenario, when=BASE_TIME, amount="12.34")
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement.lower())

    event.listen(pg_engine, "before_cursor_execute", capture_statement)
    try:
        with Session(pg_engine) as session:
            snapshot = get_book_balances(session, scenario.book_id)
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture_statement)

    balance_read_statements = [
        statement
        for statement in statements
        if any(
            f"from {table}" in statement
            for table in (
                "book_event_heads",
                "account_balances",
                "accounts",
                "journal_postings",
            )
        )
    ]
    assert len(balance_read_statements) == 3
    assert not any("journal_postings" in statement for statement in statements)
    assert snapshot.projection_matches_reference is None


def test_verified_balance_query_falls_back_to_reference_on_projection_mismatch(
    pg_engine,
) -> None:
    scenario = _seed(pg_engine)
    _post(pg_engine, scenario, when=BASE_TIME, amount="12.34")
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update account_balances set balance_units=balance_units + 1 "
                "where book_id=:book_id and account_id=:account_id"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.debit_account_id,
            },
        )

    with Session(pg_engine) as session:
        verified = get_verified_book_balances(session, scenario.book_id)

    debit_balance = {item.account_id: item for item in verified.items}[
        scenario.debit_account_id
    ]
    assert verified.projection_matches_reference is False
    assert debit_balance.raw_accounting_units == 1234


def test_explicit_as_of_head_reads_immutable_history_not_current_projection(
    pg_engine,
) -> None:
    scenario = _seed(pg_engine)
    _, head_position = _post(
        pg_engine,
        scenario,
        when=BASE_TIME,
        amount="12.34",
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update account_balances set balance_units=balance_units + 1 "
                "where book_id=:book_id and account_id=:account_id"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.debit_account_id,
            },
        )

    with Session(pg_engine) as session:
        historical = get_book_balances(
            session,
            scenario.book_id,
            as_of_book_position=head_position,
        )

    debit_balance = {item.account_id: item for item in historical.items}[
        scenario.debit_account_id
    ]
    assert historical.projection_matches_reference is None
    assert debit_balance.raw_accounting_units == 1234


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


def test_transaction_query_returns_exact_item_and_honors_as_of(pg_engine) -> None:
    try:
        from track_anywhere.queries.journal import get_journal_transaction
    except ImportError as error:
        pytest.fail(f"transaction query is missing: {error}")

    scenario = _seed(pg_engine)
    transaction_id, position = _post(
        pg_engine,
        scenario,
        when=BASE_TIME,
        amount="12.34",
    )

    with Session(pg_engine) as session:
        item = get_journal_transaction(
            session,
            scenario.book_id,
            transaction_id,
            as_of_book_position=position,
        )
        with pytest.raises(LookupError, match="Transaction not found"):
            get_journal_transaction(
                session,
                scenario.book_id,
                transaction_id,
                as_of_book_position=position - 1,
            )

    assert item.transaction_id == transaction_id
    assert item.book_position == position
    assert [posting.units for posting in item.postings] == [1234, 1234]


def test_balance_query_uses_projection_and_historical_postings_after_account_close(
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
        historical_journal = list_journal(
            session,
            scenario.book_id,
            limit=10,
            as_of_book_position=original_position,
        )

    assert {(row.account_id, row.natural_units) for row in current.items} == {
        (scenario.debit_account_id, 0),
        (scenario.credit_account_id, 0),
    }
    current_by_account = {row.account_id: row for row in current.items}
    assert current_by_account[scenario.debit_account_id].account_status == "closed"
    assert current_by_account[scenario.credit_account_id].account_status == "active"
    assert current.projection_matches_reference is None
    assert {(row.account_id, row.raw_accounting_units) for row in historical.items} == {
        (scenario.debit_account_id, 1234),
        (scenario.credit_account_id, -1234),
    }
    by_id = {item.transaction_id: item for item in journal.items}
    assert by_id[original_id].reversed_by_transaction_id == reversal_id
    assert by_id[reversal_id].reverses_transaction_id == original_id
    historical_by_id = {item.transaction_id: item for item in historical_journal.items}
    assert historical_by_id[original_id].reversed_by_transaction_id is None
    assert reversal_id not in historical_by_id
    assert outcome.result.last_book_position == 2


def test_balance_query_preserves_raw_projection_and_exposes_natural_liability(
    pg_engine,
) -> None:
    scenario = _seed(
        pg_engine,
        credit_account_type="liability",
        credit_account_subtype="credit_card",
    )
    expense_account_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, current_name, status) values "
                "(:book_id, :account_id, 'USD', 'expense', null, "
                "'Card expense', 'active')"
            ),
            {"book_id": scenario.book_id, "account_id": expense_account_id},
        )

    charge = ChargeCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        expense_account_id=expense_account_id,
        asset_code="USD",
        amount="12.34",
        effective_at=BASE_TIME,
    )
    execute_charge_credit_card(
        charge,
        raw_key=f"query-card-charge:{charge.command_id}",
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    with Session(pg_engine) as session:
        charged = get_book_balances(session, scenario.book_id)
        charged_journal = list_journal(session, scenario.book_id, limit=10)

    charged_by_id = {item.account_id: item for item in charged.items}
    card = charged_by_id[scenario.credit_account_id]
    assert card.account_type == "liability"
    assert card.account_subtype == "credit_card"
    assert card.account_status == "active"
    assert card.raw_accounting_units == -1234
    assert card.natural_units == 1234
    assert card.normal_side == "credit"
    assert card.balance_semantics == "natural_liability_balance"
    assert card.outstanding_units == 1234
    assert card.overpayment_units == 0
    expense = charged_by_id[expense_account_id]
    assert expense.account_type == "expense"
    assert expense.account_subtype is None
    assert expense.raw_accounting_units == 1234
    assert expense.natural_units == 1234
    assert expense.normal_side == "debit"
    assert expense.balance_semantics == "natural_expense_balance"
    assert expense.outstanding_units is None
    assert expense.overpayment_units is None
    assert charged.projection_matches_reference is None
    charge_item = next(
        item
        for item in charged_journal.items
        if item.transaction_id == charge.transaction_id
    )
    assert charge_item.credit_card_relation is not None
    assert charge_item.credit_card_relation.intent == "charge"
    assert charge_item.credit_card_relation.card_account_id == (
        scenario.credit_account_id
    )
    assert charge_item.credit_card_relation.counter_account_id == expense_account_id
    assert charge_item.credit_card_relation.original_transaction_id is None

    payment = PaymentCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        source_account_id=scenario.debit_account_id,
        asset_code="USD",
        amount="20.00",
        effective_at=BASE_TIME + timedelta(days=1),
    )
    execute_payment_credit_card(
        payment,
        raw_key=f"query-card-payment:{payment.command_id}",
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    with Session(pg_engine) as session:
        paid = get_book_balances(session, scenario.book_id)
        paid_journal = list_journal(session, scenario.book_id, limit=10)

    paid_card = {item.account_id: item for item in paid.items}[
        scenario.credit_account_id
    ]
    assert paid_card.raw_accounting_units == 766
    assert paid_card.natural_units == -766
    assert paid_card.outstanding_units == 0
    assert paid_card.overpayment_units == 766
    assert paid.projection_matches_reference is None
    payment_item = next(
        item
        for item in paid_journal.items
        if item.transaction_id == payment.transaction_id
    )
    assert payment_item.credit_card_relation is not None
    assert payment_item.credit_card_relation.intent == "payment"
    assert payment_item.credit_card_relation.counter_account_id == (
        scenario.debit_account_id
    )

    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update account_balances set balance_units=balance_units + 1 "
                "where book_id=:book_id and account_id=:account_id"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.credit_account_id,
            },
        )
    with Session(pg_engine) as session:
        corrupted_projection = get_verified_book_balances(session, scenario.book_id)

    safe_card = {item.account_id: item for item in corrupted_projection.items}[
        scenario.credit_account_id
    ]
    assert corrupted_projection.projection_matches_reference is False
    assert safe_card.raw_accounting_units == 766
    assert safe_card.natural_units == -766


def test_journal_query_exposes_refund_source_transaction(pg_engine) -> None:
    scenario = _seed(
        pg_engine,
        credit_account_type="liability",
        credit_account_subtype="credit_card",
    )
    expense_account_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, current_name, status) values "
                "(:book_id, :account_id, 'USD', 'expense', null, "
                "'Refund source expense', 'active')"
            ),
            {"book_id": scenario.book_id, "account_id": expense_account_id},
        )

    charge = ChargeCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        expense_account_id=expense_account_id,
        asset_code="USD",
        amount="12.34",
        effective_at=BASE_TIME,
    )
    execute_charge_credit_card(
        charge,
        raw_key=f"query-refund-charge:{charge.command_id}",
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    refund = RefundCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        original_transaction_id=charge.transaction_id,
        asset_code="USD",
        amount="2.34",
        effective_at=BASE_TIME + timedelta(days=1),
    )
    execute_refund_credit_card(
        refund,
        raw_key=f"query-refund:{refund.command_id}",
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )

    with Session(pg_engine) as session:
        journal = list_journal(session, scenario.book_id, limit=10)

    refund_item = next(
        item for item in journal.items if item.transaction_id == refund.transaction_id
    )
    assert refund_item.credit_card_relation is not None
    assert refund_item.credit_card_relation.intent == "refund"
    assert refund_item.credit_card_relation.card_account_id == (
        scenario.credit_account_id
    )
    assert refund_item.credit_card_relation.counter_account_id == expense_account_id
    assert refund_item.credit_card_relation.original_transaction_id == (
        charge.transaction_id
    )
