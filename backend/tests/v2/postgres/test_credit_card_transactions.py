from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.catalogs.close_account import (
    AccountBalanceProjectionMismatch,
    AccountBalanceNonzero,
    CloseAccount,
    close_account,
)
from track_anywhere.application.catalogs.reopen_account import (
    ReopenAccount,
    reopen_account,
)
from track_anywhere.application.credit_cards.record import (
    ChargeCreditCardCommand,
    CreditCardAccountInvalid,
    CreditCardRefundExceeded,
    CreditCardRefundSourceInvalid,
    FeeCreditCardCommand,
    PaymentCreditCardCommand,
    RefundCreditCardCommand,
    execute_charge_credit_card,
    execute_fee_credit_card,
    execute_payment_credit_card,
    execute_refund_credit_card,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.journal.correct_transaction import (
    CreditCardGeneralCorrectionForbidden,
    CorrectTransactionCommand,
    CorrectionReplacement,
    execute_correct_transaction,
)
from track_anywhere.application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingLineInput,
    UnsupportedCreditCardReporting,
    UnsupportedReportingTarget,
    execute_assign_reporting_lines,
)
from track_anywhere.application.journal.post_transaction import PostTransactionPosting
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.application.journal.reverse_transaction import (
    CreditCardChargeHasActiveRefunds,
    CreditCardReversalChainForbidden,
    CreditCardReversalPrecedesOriginal,
    CreditCardReversalRequiresActiveAccount,
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.domain.credit_cards.events import CreditCardTransactionRecorded
from track_anywhere.domain.journal.events import (
    JournalPostingFact,
    JournalTransactionPosted,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from track_anywhere.domain.journal import AccountClosed
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
)
from track_anywhere.infrastructure.db.models.credit_cards import (
    CreditCardTransactionRecord,
)
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.projections.monthly_summary import (
    cold_replay_monthly_summary,
    read_monthly_summary,
)
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.infrastructure.projections.synchronous import (
    SynchronousProjectionError,
)
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from track_anywhere.verification import verify_v2_ledger


EFFECTIVE_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _seed_card_accounts(
    engine,
    *,
    card_type: str = "liability",
    card_subtype: str | None = "credit_card",
) -> tuple[JournalScenario, UUID]:
    base = JournalScenario.create()
    seed_journal_scenario(engine, base)
    scenario = replace(
        base,
        debit_account_id=uuid4(),
        credit_account_id=uuid4(),
    )
    source_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, current_name, status) values "
                "(:book_id, :expense_id, 'USD', 'expense', null, "
                "'Card expense', 'active'), "
                "(:book_id, :card_id, 'USD', :card_type, :card_subtype, "
                "'Credit card', 'active'), "
                "(:book_id, :source_id, 'USD', 'asset', null, "
                "'Checking', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "expense_id": scenario.debit_account_id,
                "card_id": scenario.credit_account_id,
                "card_type": card_type,
                "card_subtype": card_subtype,
                "source_id": source_id,
            },
        )
    return scenario, source_id


def _actor(scenario: JournalScenario) -> CommandActor:
    return CommandActor(subject_id=scenario.actor_subject_id)


def _execute(engine, scenario: JournalScenario, command):
    functions = {
        ChargeCreditCardCommand: execute_charge_credit_card,
        FeeCreditCardCommand: execute_fee_credit_card,
        PaymentCreditCardCommand: execute_payment_credit_card,
        RefundCreditCardCommand: execute_refund_credit_card,
    }
    return functions[type(command)](
        command,
        raw_key=f"{command.operation}:{command.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(engine),
    )


def _charge(scenario: JournalScenario, amount: str = "100.00"):
    return ChargeCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        expense_account_id=scenario.debit_account_id,
        asset_code="USD",
        amount=amount,
        effective_at=EFFECTIVE_AT,
    )


def _zero_card_balance(engine) -> JournalScenario:
    scenario, source_id = _seed_card_accounts(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                'update book_members set scopes=\'["book:write","ledger:write"]\' '
                "where book_id=:book_id and user_id=:user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
    charge = _charge(scenario, "10.00")
    payment = PaymentCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        source_account_id=source_id,
        asset_code="USD",
        amount="10.00",
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    _execute(engine, scenario, charge)
    _execute(engine, scenario, payment)
    return scenario


def _seed_card_category(engine, scenario: JournalScenario) -> tuple[UUID, UUID]:
    category_id = uuid4()
    version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into categories "
                "(book_id, category_id, current_name, status) "
                "values (:book_id, :category_id, 'Card expense', 'active')"
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions "
                "(book_id, category_id, category_version_id, name, status, "
                "change_reason_code) values "
                "(:book_id, :category_id, :version_id, 'Card expense', "
                "'active', 'created')"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )
        connection.execute(
            text(
                "update categories set current_version_id=:version_id "
                "where book_id=:book_id and category_id=:category_id"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )
    return category_id, version_id


def _assign_card_expense(
    engine,
    scenario: JournalScenario,
    *,
    transaction_id: UUID,
    category_id: UUID,
    version_id: UUID,
    units: str,
    effective_at: datetime,
    line_kind: ReportingLineKind = ReportingLineKind.EXPENSE,
):
    command = AssignReportingLinesCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=transaction_id,
        expected_revision=0,
        lines=(
            ReportingLineInput(
                line_id=uuid4(),
                line_version_id=uuid4(),
                catalog_id=version_id,
                asset_code="USD",
                units=units,
                line_kind=line_kind,
                dimension=ReportingDimension.CATEGORY,
                dimension_id=category_id,
            ),
        ),
        effective_at=effective_at,
    )
    return execute_assign_reporting_lines(
        command,
        raw_key=f"card-reporting:{command.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(engine),
    )


def test_credit_card_with_nonzero_balance_cannot_be_closed(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                'update book_members set scopes=\'["book:write","ledger:write"]\' '
                "where book_id=:book_id and user_id=:user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
    _execute(pg_engine, scenario, _charge(scenario, "10.00"))

    with pytest.raises(AccountBalanceNonzero, match="zero balance"):
        close_account(
            CloseAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
            ),
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )

    with Session(pg_engine) as session:
        account = session.get(
            AccountRecord,
            (scenario.book_id, scenario.credit_account_id),
        )
        assert account is not None and account.status == "active"


def test_credit_card_with_postings_requires_a_balance_projection(
    pg_engine,
    migrated_postgres_database,
) -> None:
    scenario = _zero_card_balance(pg_engine)
    migrator_engine = create_engine(migrated_postgres_database.migrator_url)
    with migrator_engine.begin() as connection:
        connection.exec_driver_sql(
            f'SET ROLE "{migrated_postgres_database.owner_role}"'
        )
        connection.execute(
            text(
                "delete from account_balances "
                "where book_id=:book_id and account_id=:account_id "
                "and asset_code='USD'"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.credit_account_id,
            },
        )
    migrator_engine.dispose()

    with pytest.raises(AccountBalanceProjectionMismatch, match="missing"):
        close_account(
            CloseAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
            ),
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )


def test_credit_card_balance_projection_must_cover_latest_posting(
    pg_engine,
    migrated_postgres_database,
) -> None:
    scenario = _zero_card_balance(pg_engine)
    migrator_engine = create_engine(migrated_postgres_database.migrator_url)
    with migrator_engine.begin() as connection:
        connection.exec_driver_sql(
            f'SET ROLE "{migrated_postgres_database.owner_role}"'
        )
        connection.execute(
            text(
                "update account_balances set as_of_position=1 "
                "where book_id=:book_id and account_id=:account_id "
                "and asset_code='USD'"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.credit_account_id,
            },
        )
    migrator_engine.dispose()

    with pytest.raises(AccountBalanceProjectionMismatch, match="stale"):
        close_account(
            CloseAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
            ),
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )


def test_credit_card_balance_projection_must_match_independent_posting_sum(
    pg_engine,
    migrated_postgres_database,
) -> None:
    scenario = _zero_card_balance(pg_engine)
    migrator_engine = create_engine(migrated_postgres_database.migrator_url)
    with migrator_engine.begin() as connection:
        connection.exec_driver_sql(
            f'SET ROLE "{migrated_postgres_database.owner_role}"'
        )
        connection.execute(
            text(
                "update account_balances set balance_units=1 "
                "where book_id=:book_id and account_id=:account_id "
                "and asset_code='USD'"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.credit_account_id,
            },
        )
    migrator_engine.dispose()

    with pytest.raises(AccountBalanceProjectionMismatch, match="journal postings"):
        close_account(
            CloseAccount(
                book_id=scenario.book_id,
                account_id=scenario.credit_account_id,
            ),
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )


def test_zero_balance_card_can_reopen_for_delayed_refund_and_reversal(
    pg_engine,
) -> None:
    scenario, source_id = _seed_card_accounts(pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                'update book_members set scopes=\'["book:write","ledger:write"]\' '
                "where book_id=:book_id and user_id=:user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
    charge = _charge(scenario, "10.00")
    payment = PaymentCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        source_account_id=source_id,
        asset_code="USD",
        amount="10.00",
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    _execute(pg_engine, scenario, charge)
    _execute(pg_engine, scenario, payment)
    close_account(
        CloseAccount(
            book_id=scenario.book_id,
            account_id=scenario.credit_account_id,
        ),
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )

    delayed_refund = _refund(scenario, charge.transaction_id, "2.00", offset=2)
    with pytest.raises(AccountClosed):
        _execute(pg_engine, scenario, delayed_refund)
    reversal = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=payment.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=3),
    )
    with pytest.raises(CreditCardReversalRequiresActiveAccount, match="reopen"):
        execute_reverse_transaction(
            reversal,
            raw_key=f"closed-card-reversal:{reversal.command_id}",
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )

    reopened = reopen_account(
        ReopenAccount(
            book_id=scenario.book_id,
            account_id=scenario.credit_account_id,
        ),
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )
    assert reopened["status"] == "active"
    execute_reverse_transaction(
        reversal,
        raw_key=f"closed-card-reversal:{reversal.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )
    _execute(pg_engine, scenario, delayed_refund)

    with Session(pg_engine) as session:
        balance = session.get(
            AccountBalanceRecord,
            (scenario.book_id, scenario.credit_account_id, "USD"),
        )
        assert balance is not None and int(balance.balance_units) == -800


def test_typed_card_reporting_uses_economic_signs_and_target_months(
    pg_engine,
) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    category_id, version_id = _seed_card_category(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    worker = AsyncProjectionWorker(factory)

    charge = replace(
        _charge(scenario, "10.00"),
        effective_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    _execute(pg_engine, scenario, charge)
    worker.run_once(scenario.book_id)
    _assign_card_expense(
        pg_engine,
        scenario,
        transaction_id=charge.transaction_id,
        category_id=category_id,
        version_id=version_id,
        units="1000",
        effective_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    worker.run_once(scenario.book_id)

    refund = RefundCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        original_transaction_id=charge.transaction_id,
        asset_code="USD",
        amount="4.00",
        effective_at=datetime(2026, 2, 5, tzinfo=UTC),
    )
    _execute(pg_engine, scenario, refund)
    worker.run_once(scenario.book_id)
    _assign_card_expense(
        pg_engine,
        scenario,
        transaction_id=refund.transaction_id,
        category_id=category_id,
        version_id=version_id,
        units="400",
        effective_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    worker.run_once(scenario.book_id)

    reversal = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=refund.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.PROVIDER_REVERSAL,
        effective_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    execute_reverse_transaction(
        reversal,
        raw_key=f"card-refund-reversal:{reversal.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )
    worker.run_once(scenario.book_id)

    fee = FeeCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        expense_account_id=scenario.debit_account_id,
        asset_code="USD",
        amount="2.00",
        effective_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    _execute(pg_engine, scenario, fee)
    _assign_card_expense(
        pg_engine,
        scenario,
        transaction_id=fee.transaction_id,
        category_id=category_id,
        version_id=version_id,
        units="200",
        effective_at=datetime(2026, 5, 2, tzinfo=UTC),
    )
    worker.run_once(scenario.book_id)

    with Session(pg_engine) as session:
        cold = cold_replay_monthly_summary(session, scenario.book_id)
        online = {
            period: read_monthly_summary(
                session,
                scenario.book_id,
                period_start=period,
            )
            for period in (
                date(2026, 1, 1),
                date(2026, 2, 1),
                date(2026, 4, 1),
                date(2026, 5, 1),
            )
        }
    assert online == {period: cold[period] for period in online}
    assert online[date(2026, 1, 1)][0].units == 1000
    assert online[date(2026, 2, 1)][0].units == -400
    assert online[date(2026, 4, 1)][0].units == 400
    assert online[date(2026, 5, 1)][0].units == 200


def test_card_reporting_rejects_payments_and_nonexpense_lines(pg_engine) -> None:
    scenario, source_id = _seed_card_accounts(pg_engine)
    category_id, version_id = _seed_card_category(pg_engine, scenario)
    charge = _charge(scenario, "5.00")
    payment = PaymentCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        source_account_id=source_id,
        asset_code="USD",
        amount="1.00",
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    _execute(pg_engine, scenario, charge)
    _execute(pg_engine, scenario, payment)

    with pytest.raises(UnsupportedCreditCardReporting, match="transfers"):
        _assign_card_expense(
            pg_engine,
            scenario,
            transaction_id=payment.transaction_id,
            category_id=category_id,
            version_id=version_id,
            units="100",
            effective_at=EFFECTIVE_AT + timedelta(minutes=2),
        )
    with pytest.raises(UnsupportedCreditCardReporting, match="expense reporting"):
        _assign_card_expense(
            pg_engine,
            scenario,
            transaction_id=charge.transaction_id,
            category_id=category_id,
            version_id=version_id,
            units="500",
            effective_at=EFFECTIVE_AT + timedelta(minutes=3),
            line_kind=ReportingLineKind.INCOME,
        )

    with Session(pg_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ReportingLineRecord)) == 0
        )


def test_card_reversal_cannot_be_classified_a_second_time(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    category_id, version_id = _seed_card_category(pg_engine, scenario)
    charge = _charge(scenario, "5.00")
    _execute(pg_engine, scenario, charge)
    reversal = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=charge.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    execute_reverse_transaction(
        reversal,
        raw_key=f"card-reporting-reversal:{reversal.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )

    with pytest.raises(UnsupportedReportingTarget, match="inherit"):
        _assign_card_expense(
            pg_engine,
            scenario,
            transaction_id=reversal.reversal_transaction_id,
            category_id=category_id,
            version_id=version_id,
            units="500",
            effective_at=EFFECTIVE_AT + timedelta(minutes=2),
        )


def test_projector_rejects_bypassed_payment_reporting_event(pg_engine) -> None:
    scenario, source_id = _seed_card_accounts(pg_engine)
    category_id, version_id = _seed_card_category(pg_engine, scenario)
    payment = PaymentCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        source_account_id=source_id,
        asset_code="USD",
        amount="1.00",
        effective_at=EFFECTIVE_AT,
    )
    _execute(pg_engine, scenario, payment)
    command_id = uuid4()
    pending = PendingEvent(
        event_id=uuid4(),
        stream_type="reporting_lines",
        stream_id=payment.transaction_id,
        payload=ReportingLinesAssigned(
            transaction_id=payment.transaction_id,
            classification_revision=1,
            lines=(
                ReportingLine(
                    line_id=uuid4(),
                    line_version_id=uuid4(),
                    catalog_id=version_id,
                    position=0,
                    asset_code="USD",
                    units="100",
                    line_kind=ReportingLineKind.EXPENSE,
                    dimension=ReportingDimension.CATEGORY,
                    dimension_id=category_id,
                ),
            ),
        ),
        command_id=command_id,
        actor_subject_id=scenario.actor_subject_id,
        correlation_id=command_id,
        causation_event_id=None,
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )

    with pytest.raises(SynchronousProjectionError, match="payments"):
        with Session(pg_engine) as session, session.begin():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(session, scenario.book_id)
            committer.append_and_project(
                session,
                locked_head=locked,
                expected_stream_versions={(pending.stream_type, pending.stream_id): 0},
                events=(pending,),
            )


def _refund(
    scenario: JournalScenario,
    original_transaction_id: UUID,
    amount: str,
    *,
    offset: int,
):
    return RefundCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        original_transaction_id=original_transaction_id,
        asset_code="USD",
        amount=amount,
        effective_at=EFFECTIVE_AT + timedelta(minutes=offset),
    )


def _generic_correction(
    scenario: JournalScenario,
    transaction_id: UUID,
) -> CorrectTransactionCommand:
    return CorrectTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reverses_transaction_id=transaction_id,
        reversal_transaction_id=uuid4(),
        expected_reversal_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        reversal_effective_at=EFFECTIVE_AT + timedelta(minutes=10),
        replacement=CorrectionReplacement(
            transaction_id=uuid4(),
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
            effective_at=EFFECTIVE_AT + timedelta(minutes=11),
        ),
    )


def _execute_correction(
    engine,
    scenario: JournalScenario,
    command: CorrectTransactionCommand,
):
    return execute_correct_transaction(
        command,
        raw_key=f"correct:{command.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(engine),
    )


def _bypassed_reversal_pending(
    engine,
    scenario: JournalScenario,
    *,
    source_transaction_id: UUID,
    effective_at: datetime,
) -> PendingEvent:
    with Session(engine) as session:
        source_transaction = session.get(
            JournalTransactionRecord,
            (scenario.book_id, source_transaction_id),
        )
        assert source_transaction is not None
        source_event = session.get(
            LedgerEventRecord, source_transaction.source_event_id
        )
        assert source_event is not None
        source_payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            source_event.event_type,
            source_event.event_schema_version,
            source_event.payload,
        )
        if type(source_payload) is CreditCardTransactionRecorded:
            source_postings = source_payload.postings
        elif type(source_payload) is JournalTransactionPosted:
            source_postings = source_payload.postings
        else:
            assert type(source_payload) is JournalTransactionReversed
            source_postings = source_payload.inverse_postings
        reversal_transaction_id = uuid4()
        command_id = uuid4()
        return PendingEvent(
            event_id=uuid4(),
            stream_type="journal_transaction",
            stream_id=reversal_transaction_id,
            payload=JournalTransactionReversed(
                reversal_transaction_id=reversal_transaction_id,
                reverses_transaction_id=source_transaction_id,
                original_event_id=source_event.event_id,
                original_event_hash=source_event.event_hash.hex(),
                reason_code=ReversalReasonCode.USER_CORRECTION,
                inverse_postings=tuple(
                    JournalPostingFact(
                        posting_id=uuid4(),
                        position=posting.position,
                        account_id=posting.account_id,
                        asset_code=posting.asset_code,
                        side=(
                            PostingSide.CREDIT
                            if posting.side is PostingSide.DEBIT
                            else PostingSide.DEBIT
                        ),
                        units=posting.units,
                    )
                    for posting in source_postings
                ),
            ),
            command_id=command_id,
            actor_subject_id=scenario.actor_subject_id,
            correlation_id=command_id,
            causation_event_id=source_event.event_id,
            effective_at=effective_at,
        )


def _append_bypassed_reversal(engine, scenario, pending: PendingEvent) -> None:
    with Session(engine) as session, session.begin():
        committer = LedgerCommitter()
        locked = committer.execute_under_book_lock(session, scenario.book_id)
        committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={(pending.stream_type, pending.stream_id): 0},
            events=(pending,),
        )


def test_charge_payment_and_fee_write_typed_events_with_canonical_postings(
    pg_engine,
) -> None:
    scenario, source_id = _seed_card_accounts(pg_engine)
    charge = _charge(scenario, "12.34")
    payment = PaymentCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        source_account_id=source_id,
        asset_code="USD",
        amount="5.00",
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    fee = FeeCreditCardCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=uuid4(),
        expected_stream_version=0,
        card_account_id=scenario.credit_account_id,
        expense_account_id=scenario.debit_account_id,
        asset_code="USD",
        amount="1.00",
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )

    for command in (charge, payment, fee):
        outcome = _execute(pg_engine, scenario, command)
        assert outcome.result.status_code == 201

    with Session(pg_engine) as session:
        events = tuple(
            session.scalars(
                select(LedgerEventRecord).order_by(LedgerEventRecord.book_position)
            )
        )
        assert [event.event_type for event in events] == [
            "CreditCardTransactionRecorded",
        ] * 3
        assert [event.payload["intent"] for event in events] == [
            "charge",
            "payment",
            "fee",
        ]
        transactions = tuple(
            session.scalars(
                select(JournalTransactionRecord).order_by(
                    JournalTransactionRecord.source_position
                )
            )
        )
        assert [row.transaction_kind for row in transactions] == [
            "credit_card_charge",
            "credit_card_payment",
            "credit_card_fee",
        ]
        postings = tuple(
            session.scalars(
                select(JournalPostingRecord).order_by(
                    JournalPostingRecord.transaction_id,
                    JournalPostingRecord.posting_position,
                )
            )
        )
        assert all(int(posting.units) > 0 for posting in postings)
        card_balance = session.get(
            AccountBalanceRecord,
            (scenario.book_id, scenario.credit_account_id, "USD"),
        )
        assert card_balance is not None
        assert int(card_balance.balance_units) == -834
        assert (
            session.scalar(
                select(func.count()).select_from(CreditCardTransactionRecord)
            )
            == 3
        )


def test_independent_verifier_accepts_typed_credit_card_projection(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario, "12.34")
    _execute(pg_engine, scenario, charge)

    report = verify_v2_ledger(pg_engine.url.render_as_string(hide_password=False))

    assert report.status == "PASS", report.issues
    assert report.counts["credit_card_transactions"] == 1
    assert "credit_cards" in report.projection_hashes


def test_independent_verifier_uses_a_fixed_number_of_streaming_queries(
    pg_engine,
) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    _execute(pg_engine, scenario, _charge(scenario, "12.34"))
    extra_book_ids = [uuid4() for _ in range(24)]
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into books "
                "(book_id, current_name, base_asset_code, write_state) "
                "values (:book_id, 'Verifier scale', null, 'active')"
            ),
            [{"book_id": book_id} for book_id in extra_book_ids],
        )
        connection.execute(
            text(
                "insert into book_event_heads (book_id, last_position, last_hash) "
                "values (:book_id, 0, :zero_hash)"
            ),
            [
                {"book_id": book_id, "zero_hash": bytes(32)}
                for book_id in extra_book_ids
            ],
        )

    select_count = 0
    streamed_tables: set[str] = set()
    stream_markers = {
        "book_event_heads": "book_event_heads.last_position",
        "event_stream_heads": "event_stream_heads.last_event_id",
        "ledger_events": "ledger_events.payload",
        "journal_transactions": "journal_transactions.effective_at",
        "journal_postings": "journal_postings.posting_id",
        "account_balances": "account_balances.balance_units",
        "credit_card_transactions": "credit_card_transactions.card_account_id",
    }

    def capture_selects(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        context,
        _executemany,
    ) -> None:
        nonlocal select_count
        normalized = " ".join(statement.casefold().split())
        if not normalized.startswith("select"):
            return
        select_count += 1
        if context.execution_options.get("yield_per"):
            for table, marker in stream_markers.items():
                if marker in normalized:
                    streamed_tables.add(table)

    event.listen(Engine, "before_cursor_execute", capture_selects)
    try:
        report = verify_v2_ledger(pg_engine.url.render_as_string(hide_password=False))
    finally:
        event.remove(Engine, "before_cursor_execute", capture_selects)

    assert report.status == "PASS", report.issues
    assert report.counts["books"] == 25
    assert select_count <= 10
    assert streamed_tables == {
        "book_event_heads",
        "event_stream_heads",
        "ledger_events",
        "journal_transactions",
        "journal_postings",
        "account_balances",
        "credit_card_transactions",
    }


def test_refund_cap_excludes_reversed_refunds_and_allows_exact_full_refund(
    pg_engine,
) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    _execute(pg_engine, scenario, charge)
    first = _refund(scenario, charge.transaction_id, "40.00", offset=1)
    second = _refund(scenario, charge.transaction_id, "60.00", offset=2)
    _execute(pg_engine, scenario, first)
    _execute(pg_engine, scenario, second)

    with pytest.raises(CreditCardRefundExceeded):
        _execute(
            pg_engine,
            scenario,
            _refund(scenario, charge.transaction_id, "0.01", offset=3),
        )

    reversal = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=second.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=4),
    )
    execute_reverse_transaction(
        reversal,
        raw_key=f"reverse:{reversal.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )
    replacement = _refund(scenario, charge.transaction_id, "60.00", offset=5)
    _execute(pg_engine, scenario, replacement)

    with Session(pg_engine) as session:
        projected = session.get(
            JournalTransactionRecord,
            (scenario.book_id, reversal.reversal_transaction_id),
        )
        assert projected is not None
        assert projected.transaction_kind == "credit_card_refund"
        card_balance = session.get(
            AccountBalanceRecord,
            (scenario.book_id, scenario.credit_account_id, "USD"),
        )
        assert card_balance is not None and int(card_balance.balance_units) == 0


def test_charge_reversal_requires_active_refunds_to_be_reversed_first(
    pg_engine,
) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    refund = _refund(scenario, charge.transaction_id, "25.00", offset=1)
    _execute(pg_engine, scenario, charge)
    _execute(pg_engine, scenario, refund)

    reverse_charge = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=charge.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )
    with pytest.raises(CreditCardChargeHasActiveRefunds):
        execute_reverse_transaction(
            reverse_charge,
            raw_key=f"reverse:{reverse_charge.command_id}",
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )

    reverse_refund = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=refund.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=3),
    )
    execute_reverse_transaction(
        reverse_refund,
        raw_key=f"reverse:{reverse_refund.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )
    execute_reverse_transaction(
        reverse_charge,
        raw_key=f"reverse:{reverse_charge.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )

    with Session(pg_engine) as session:
        card_balance = session.get(
            AccountBalanceRecord,
            (scenario.book_id, scenario.credit_account_id, "USD"),
        )
        assert card_balance is not None and int(card_balance.balance_units) == 0


def test_refund_and_reversal_cannot_precede_the_typed_card_source(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    _execute(pg_engine, scenario, charge)
    early_refund = _refund(scenario, charge.transaction_id, "1.00", offset=-1)
    with pytest.raises(CreditCardRefundSourceInvalid):
        _execute(pg_engine, scenario, early_refund)

    early_reversal = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=charge.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT - timedelta(minutes=1),
    )
    with pytest.raises(CreditCardReversalPrecedesOriginal):
        execute_reverse_transaction(
            early_reversal,
            raw_key=f"reverse:{early_reversal.command_id}",
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )


def test_credit_card_reversal_cannot_be_reversed_again(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    refund = _refund(scenario, charge.transaction_id, "25.00", offset=1)
    _execute(pg_engine, scenario, charge)
    _execute(pg_engine, scenario, refund)
    reverse_refund = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=refund.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )
    execute_reverse_transaction(
        reverse_refund,
        raw_key=f"reverse:{reverse_refund.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )
    third_level = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=reverse_refund.reversal_transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=3),
    )

    with pytest.raises(CreditCardReversalChainForbidden):
        execute_reverse_transaction(
            third_level,
            raw_key=f"reverse:{third_level.command_id}",
            actor=_actor(scenario),
            uow_factory=_uow_factory(pg_engine),
        )

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 3


@pytest.mark.parametrize("intent", ["charge", "payment", "refund", "fee"])
def test_general_correction_rejects_each_typed_credit_card_intent(
    pg_engine,
    intent: str,
) -> None:
    scenario, source_id = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    if intent == "charge":
        target = charge
    elif intent == "payment":
        target = PaymentCreditCardCommand(
            book_id=scenario.book_id,
            command_id=uuid4(),
            transaction_id=uuid4(),
            expected_stream_version=0,
            card_account_id=scenario.credit_account_id,
            source_account_id=source_id,
            asset_code="USD",
            amount="10.00",
            effective_at=EFFECTIVE_AT,
        )
    elif intent == "refund":
        _execute(pg_engine, scenario, charge)
        target = _refund(scenario, charge.transaction_id, "10.00", offset=1)
    else:
        target = FeeCreditCardCommand(
            book_id=scenario.book_id,
            command_id=uuid4(),
            transaction_id=uuid4(),
            expected_stream_version=0,
            card_account_id=scenario.credit_account_id,
            expense_account_id=scenario.debit_account_id,
            asset_code="USD",
            amount="10.00",
            effective_at=EFFECTIVE_AT,
        )
    _execute(pg_engine, scenario, target)
    correction = _generic_correction(scenario, target.transaction_id)

    with pytest.raises(
        CreditCardGeneralCorrectionForbidden,
        match="typed credit-card transactions cannot use general correction",
    ):
        _execute_correction(pg_engine, scenario, correction)


def test_rejected_refund_correction_cannot_release_refund_capacity(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    refund = _refund(scenario, charge.transaction_id, "40.00", offset=1)
    _execute(pg_engine, scenario, charge)
    _execute(pg_engine, scenario, refund)

    with pytest.raises(
        CreditCardGeneralCorrectionForbidden,
        match="typed credit-card transactions cannot use general correction",
    ):
        _execute_correction(
            pg_engine,
            scenario,
            _generic_correction(scenario, refund.transaction_id),
        )

    with pytest.raises(CreditCardRefundExceeded):
        _execute(
            pg_engine,
            scenario,
            _refund(scenario, charge.transaction_id, "60.01", offset=2),
        )
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 2


def test_general_correction_rejects_a_typed_credit_card_reversal(pg_engine) -> None:
    scenario, _ = _seed_card_accounts(pg_engine)
    charge = _charge(scenario)
    _execute(pg_engine, scenario, charge)
    reversal = ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reversal_transaction_id=uuid4(),
        reverses_transaction_id=charge.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )
    execute_reverse_transaction(
        reversal,
        raw_key=f"reverse:{reversal.command_id}",
        actor=_actor(scenario),
        uow_factory=_uow_factory(pg_engine),
    )

    with pytest.raises(
        CreditCardGeneralCorrectionForbidden,
        match="typed credit-card transactions cannot use general correction",
    ):
        _execute_correction(
            pg_engine,
            scenario,
            _generic_correction(scenario, reversal.reversal_transaction_id),
        )


@pytest.mark.parametrize(
    ("card_type", "card_subtype"),
    [("asset", "loan"), ("liability", None), ("liability", "loan")],
)
def test_rejects_accounts_that_are_not_credit_card_liabilities(
    pg_engine,
    card_type: str,
    card_subtype: str | None,
) -> None:
    scenario, _ = _seed_card_accounts(
        pg_engine,
        card_type=card_type,
        card_subtype=card_subtype,
    )

    with pytest.raises(CreditCardAccountInvalid):
        _execute(pg_engine, scenario, _charge(scenario))

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 0
