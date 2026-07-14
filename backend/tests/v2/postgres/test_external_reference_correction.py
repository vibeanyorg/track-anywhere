from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.correct_external_reference import (
    CorrectExternalReferenceCommand,
    ExternalReferenceUnchanged,
    execute_correct_external_reference,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.journal.reverse_transaction import TransactionNotFound
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.event_store import StreamVersionConflict
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    TransactionExternalReferenceRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


EFFECTIVE_AT = datetime(2026, 7, 14, 17, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _post_with_reference(engine, scenario: JournalScenario) -> None:
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
            external_references=(
                FinancialExternalReference(
                    provider_code="stripe",
                    kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                    reference="pi_original",
                ),
            ),
        ),
        raw_key=f"post:{scenario.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _command(
    scenario: JournalScenario,
    *,
    expected_stream_version: int,
    corrected_reference: str,
) -> CorrectExternalReferenceCommand:
    return CorrectExternalReferenceCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=scenario.transaction_id,
        provider_code="stripe",
        reference_kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
        corrected_reference=corrected_reference,
        expected_stream_version=expected_stream_version,
        effective_at=EFFECTIVE_AT + timedelta(minutes=expected_stream_version + 1),
    )


def _execute(engine, scenario, command):
    return execute_correct_external_reference(
        command,
        raw_key=f"reference:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _balances(session):
    return tuple(
        sorted(
            (row.account_id, row.asset_code, int(row.balance_units), row.as_of_position)
            for row in session.scalars(select(AccountBalanceRecord))
        )
    )


def test_correction_updates_only_current_reference_and_keeps_event_history(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_with_reference(pg_engine, scenario)
    with Session(pg_engine) as session:
        balances_before = _balances(session)
    first = _command(
        scenario,
        expected_stream_version=0,
        corrected_reference="pi_corrected",
    )
    second = _command(
        scenario,
        expected_stream_version=1,
        corrected_reference="pi_final",
    )

    first_outcome = _execute(pg_engine, scenario, first)
    _execute(pg_engine, scenario, second)

    assert first_outcome.result.body == {
        "transaction_id": str(scenario.transaction_id),
        "provider_code": "stripe",
        "reference_kind": "provider_transaction",
        "reference": "pi_corrected",
        "as_of_book_position": 2,
    }
    with Session(pg_engine) as session:
        row = session.get(
            TransactionExternalReferenceRecord,
            (
                scenario.book_id,
                scenario.transaction_id,
                "stripe",
                "provider_transaction",
            ),
        )
        assert row is not None
        assert row.reference_value == "pi_final"
        corrections = tuple(
            session.scalars(
                select(LedgerEventRecord)
                .where(
                    LedgerEventRecord.event_type
                    == "FinancialExternalReferenceCorrected"
                )
                .order_by(LedgerEventRecord.book_position)
            )
        )
        assert [event.payload["previous_reference"] for event in corrections] == [
            "pi_original",
            "pi_corrected",
        ]
        assert [event.payload["corrected_reference"] for event in corrections] == [
            "pi_corrected",
            "pi_final",
        ]
        original = session.scalar(
            select(LedgerEventRecord).where(
                LedgerEventRecord.event_type == "JournalTransactionPosted"
            )
        )
        assert original is not None
        assert original.payload["external_references"][0]["reference"] == "pi_original"
        assert _balances(session) == balances_before


def test_stale_reference_stream_version_rolls_back_without_changing_current_value(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_with_reference(pg_engine, scenario)
    _execute(
        pg_engine,
        scenario,
        _command(
            scenario,
            expected_stream_version=0,
            corrected_reference="pi_corrected",
        ),
    )

    with pytest.raises(StreamVersionConflict):
        _execute(
            pg_engine,
            scenario,
            _command(
                scenario,
                expected_stream_version=0,
                corrected_reference="pi_stale",
            ),
        )

    with Session(pg_engine) as session:
        row = session.scalar(select(TransactionExternalReferenceRecord))
        assert row is not None and row.reference_value == "pi_corrected"
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 2


def test_rejects_unknown_or_cross_book_transaction(pg_engine) -> None:
    first = JournalScenario.create()
    second = replace(
        JournalScenario.create(),
        actor_subject_id="human:reference-second",
    )
    seed_journal_scenario(pg_engine, first)
    seed_journal_scenario(pg_engine, second)
    _post_with_reference(pg_engine, first)
    command = replace(
        _command(
            second,
            expected_stream_version=0,
            corrected_reference="pi_other",
        ),
        transaction_id=first.transaction_id,
    )

    with pytest.raises(TransactionNotFound):
        _execute(pg_engine, second, command)


def test_rejects_noop_correction_without_partial_state(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_with_reference(pg_engine, scenario)
    command = _command(
        scenario,
        expected_stream_version=0,
        corrected_reference="pi_original",
    )

    with pytest.raises(ExternalReferenceUnchanged):
        _execute(pg_engine, scenario, command)

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )
