from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.correct_transaction import (
    CorrectTransactionCommand,
    CorrectionReplacement,
    execute_correct_transaction,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    TransactionAlreadyReversed,
    TransactionIdAlreadyExists,
    TransactionNotFound,
    execute_reverse_transaction,
)
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.journal.events import (
    JournalPostingFact,
    JournalTransactionReversed,
    ReversalReasonCode,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.journal.validators import UnbalancedAsset
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalTransactionRecord,
    TransactionReversalRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.projections.synchronous import (
    SynchronousProjectionError,
    SynchronousProjector,
)


EFFECTIVE_AT = datetime(2026, 7, 14, 12, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _post_command(
    scenario: JournalScenario,
    *,
    command_id=None,
    transaction_id=None,
    debit_amount: str = "12.34",
    credit_amount: str = "12.34",
) -> PostTransactionCommand:
    return PostTransactionCommand(
        book_id=scenario.book_id,
        command_id=command_id or scenario.command_id,
        transaction_id=transaction_id or scenario.transaction_id,
        expected_stream_version=0,
        kind=TransactionKind.STANDARD,
        postings=(
            PostTransactionPosting(
                posting_id=uuid4(),
                account_id=scenario.debit_account_id,
                asset_code="USD",
                side=PostingSide.DEBIT,
                amount=debit_amount,
            ),
            PostTransactionPosting(
                posting_id=uuid4(),
                account_id=scenario.credit_account_id,
                asset_code="USD",
                side=PostingSide.CREDIT,
                amount=credit_amount,
            ),
        ),
        effective_at=EFFECTIVE_AT,
    )


def _post_original(engine, scenario: JournalScenario) -> None:
    execute_post_transaction(
        _post_command(scenario),
        raw_key=f"post:{scenario.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _reverse_command(
    scenario: JournalScenario,
    *,
    command_id=None,
    reversal_transaction_id=None,
    reverses_transaction_id=None,
    effective_at: datetime = EFFECTIVE_AT + timedelta(minutes=1),
) -> ReverseTransactionCommand:
    return ReverseTransactionCommand(
        book_id=scenario.book_id,
        command_id=command_id or uuid4(),
        reversal_transaction_id=reversal_transaction_id or uuid4(),
        reverses_transaction_id=reverses_transaction_id or scenario.transaction_id,
        expected_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        effective_at=effective_at,
    )


def _execute_reverse(engine, scenario, command):
    return execute_reverse_transaction(
        command,
        raw_key=f"reverse:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def test_reversal_derives_exact_inverse_and_original_provenance(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    command = _reverse_command(scenario)

    outcome = _execute_reverse(pg_engine, scenario, command)

    assert outcome.replayed is False
    assert outcome.result.body == {
        "reversal_transaction_id": str(command.reversal_transaction_id),
        "reverses_transaction_id": str(scenario.transaction_id),
        "as_of_book_position": 2,
    }
    assert (outcome.result.first_book_position, outcome.result.last_book_position) == (
        2,
        2,
    )
    with Session(pg_engine) as session:
        events = tuple(
            session.scalars(
                select(LedgerEventRecord).order_by(LedgerEventRecord.book_position)
            )
        )
        original, reversal = events
        assert reversal.event_type == "JournalTransactionReversed"
        assert reversal.causation_event_id == original.event_id
        assert reversal.payload["original_event_id"] == str(original.event_id)
        assert reversal.payload["original_event_hash"] == original.event_hash.hex()
        assert reversal.payload["reverses_transaction_id"] == str(
            scenario.transaction_id
        )
        assert [posting["side"] for posting in original.payload["postings"]] == [
            "debit",
            "credit",
        ]
        assert [
            posting["side"] for posting in reversal.payload["inverse_postings"]
        ] == [
            "credit",
            "debit",
        ]
        assert [
            posting["units"] for posting in reversal.payload["inverse_postings"]
        ] == [posting["units"] for posting in original.payload["postings"]]
        assert [
            (posting["account_id"], posting["asset_code"], posting["position"])
            for posting in reversal.payload["inverse_postings"]
        ] == [
            (posting["account_id"], posting["asset_code"], posting["position"])
            for posting in original.payload["postings"]
        ]
        assert {
            posting["posting_id"] for posting in reversal.payload["inverse_postings"]
        }.isdisjoint(
            {posting["posting_id"] for posting in original.payload["postings"]}
        )

        projected = session.get(
            JournalTransactionRecord,
            (scenario.book_id, command.reversal_transaction_id),
        )
        relation = session.get(
            TransactionReversalRecord,
            (scenario.book_id, command.reversal_transaction_id),
        )
        assert projected is not None
        assert projected.transaction_kind == TransactionKind.STANDARD.value
        assert relation is not None
        assert relation.original_transaction_id == scenario.transaction_id
        assert relation.original_event_id == original.event_id
        assert relation.original_event_hash == original.event_hash
        balances = tuple(session.scalars(select(AccountBalanceRecord)))
        assert {int(balance.balance_units) for balance in balances} == {0}


def test_a_reversal_can_be_reversed_without_creating_a_cycle(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    first = _reverse_command(scenario)
    _execute_reverse(pg_engine, scenario, first)
    second = _reverse_command(
        scenario,
        reverses_transaction_id=first.reversal_transaction_id,
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )

    _execute_reverse(pg_engine, scenario, second)

    with Session(pg_engine) as session:
        relations = tuple(
            session.scalars(
                select(TransactionReversalRecord).order_by(
                    TransactionReversalRecord.source_event_id
                )
            )
        )
        assert {
            (row.reversal_transaction_id, row.original_transaction_id)
            for row in relations
        } == {
            (first.reversal_transaction_id, scenario.transaction_id),
            (second.reversal_transaction_id, first.reversal_transaction_id),
        }
        balances = tuple(session.scalars(select(AccountBalanceRecord)))
        assert sorted(int(balance.balance_units) for balance in balances) == [
            -1234,
            1234,
        ]


def test_rejects_a_second_reversal_of_the_same_target(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    _execute_reverse(pg_engine, scenario, _reverse_command(scenario))

    with pytest.raises(TransactionAlreadyReversed):
        _execute_reverse(pg_engine, scenario, _reverse_command(scenario))

    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 2
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 2
        )


def test_rejects_cross_book_target_without_disclosing_it(pg_engine) -> None:
    first = JournalScenario.create()
    second = replace(
        JournalScenario.create(),
        actor_subject_id="human:sync-test-second",
    )
    seed_journal_scenario(pg_engine, first)
    seed_journal_scenario(pg_engine, second)
    _post_original(pg_engine, first)
    command = _reverse_command(
        second,
        reverses_transaction_id=first.transaction_id,
    )

    with pytest.raises(TransactionNotFound):
        _execute_reverse(pg_engine, second, command)


def test_rejects_reusing_an_existing_transaction_id_for_the_reversal(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    command = _reverse_command(
        scenario,
        reversal_transaction_id=scenario.transaction_id,
    )

    with pytest.raises(TransactionIdAlreadyExists):
        _execute_reverse(pg_engine, scenario, command)


def test_exact_reversal_remains_available_after_an_account_is_closed(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update accounts set status = 'closed' "
                "where book_id = :book_id and account_id = :account_id"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": scenario.debit_account_id,
            },
        )

    _execute_reverse(pg_engine, scenario, _reverse_command(scenario))

    with Session(pg_engine) as session:
        balances = tuple(session.scalars(select(AccountBalanceRecord)))
        assert {int(balance.balance_units) for balance in balances} == {0}


def test_projector_rejects_a_tampered_inverse_and_rolls_back_append(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    with Session(pg_engine) as session:
        original = session.scalar(select(LedgerEventRecord))
        assert original is not None
        original_postings = original.payload["postings"]
    reversal_transaction_id = uuid4()
    command_id = uuid4()
    pending = PendingEvent(
        event_id=uuid4(),
        stream_type="journal_transaction",
        stream_id=reversal_transaction_id,
        payload=JournalTransactionReversed(
            reversal_transaction_id=reversal_transaction_id,
            reverses_transaction_id=scenario.transaction_id,
            original_event_id=original.event_id,
            original_event_hash=original.event_hash.hex(),
            reason_code=ReversalReasonCode.USER_CORRECTION,
            inverse_postings=tuple(
                JournalPostingFact(
                    posting_id=uuid4(),
                    position=posting["position"],
                    account_id=posting["account_id"],
                    asset_code=posting["asset_code"],
                    side=(
                        PostingSide.CREDIT
                        if posting["side"] == "debit"
                        else PostingSide.DEBIT
                    ),
                    units=(
                        str(int(posting["units"]) + 1)
                        if posting["position"] == 0
                        else posting["units"]
                    ),
                )
                for posting in original_postings
            ),
        ),
        command_id=command_id,
        actor_subject_id=scenario.actor_subject_id,
        correlation_id=command_id,
        causation_event_id=original.event_id,
        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
    )

    with pytest.raises(SynchronousProjectionError):
        with Session(pg_engine) as session, session.begin():
            committer = LedgerCommitter()
            locked = committer.execute_under_book_lock(session, scenario.book_id)
            committer.append_and_project(
                session,
                locked_head=locked,
                expected_stream_versions={
                    ("journal_transaction", reversal_transaction_id): 0
                },
                events=(pending,),
            )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 1
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(TransactionReversalRecord))
            == 0
        )


def test_correction_appends_reversal_and_replacement_as_one_receipt(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    replacement_id = uuid4()
    reversal_id = uuid4()
    command = CorrectTransactionCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        reverses_transaction_id=scenario.transaction_id,
        reversal_transaction_id=reversal_id,
        expected_reversal_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        reversal_effective_at=EFFECTIVE_AT + timedelta(minutes=1),
        replacement=CorrectionReplacement(
            transaction_id=replacement_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=_post_command(
                scenario,
                transaction_id=replacement_id,
                debit_amount="20.00",
                credit_amount="20.00",
            ).postings,
            effective_at=EFFECTIVE_AT + timedelta(minutes=2),
        ),
    )

    outcome = execute_correct_transaction(
        command,
        raw_key=f"correct:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(pg_engine),
    )

    assert outcome.result.body == {
        "reversal_transaction_id": str(reversal_id),
        "replacement_transaction_id": str(replacement_id),
        "reverses_transaction_id": str(scenario.transaction_id),
        "as_of_book_position": 3,
    }
    assert (outcome.result.first_book_position, outcome.result.last_book_position) == (
        2,
        3,
    )
    with Session(pg_engine) as session:
        events = tuple(
            session.scalars(
                select(LedgerEventRecord).order_by(LedgerEventRecord.book_position)
            )
        )
        assert [event.event_type for event in events] == [
            "JournalTransactionPosted",
            "JournalTransactionReversed",
            "JournalTransactionPosted",
        ]
        assert events[1].command_id == events[2].command_id == command.command_id
        assert events[2].causation_event_id == events[1].event_id
        balances = {
            row.account_id: int(row.balance_units)
            for row in session.scalars(select(AccountBalanceRecord))
        }
        assert balances == {
            scenario.debit_account_id: 2000,
            scenario.credit_account_id: -2000,
        }
        receipt = session.scalar(
            select(CommandReceiptRecord).where(
                CommandReceiptRecord.command_id == command.command_id
            )
        )
        assert receipt is not None
        assert (receipt.first_book_position, receipt.last_book_position) == (2, 3)


def test_invalid_replacement_rolls_back_the_entire_correction(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    command_id = uuid4()
    replacement = _post_command(
        scenario,
        command_id=command_id,
        transaction_id=uuid4(),
        debit_amount="20.00",
        credit_amount="19.99",
    )
    command = CorrectTransactionCommand(
        book_id=scenario.book_id,
        command_id=command_id,
        reverses_transaction_id=scenario.transaction_id,
        reversal_transaction_id=uuid4(),
        expected_reversal_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        reversal_effective_at=EFFECTIVE_AT + timedelta(minutes=1),
        replacement=CorrectionReplacement(
            transaction_id=replacement.transaction_id,
            expected_stream_version=0,
            kind=replacement.kind,
            postings=replacement.postings,
            effective_at=EFFECTIVE_AT + timedelta(minutes=2),
        ),
    )

    with pytest.raises(UnbalancedAsset):
        execute_correct_transaction(
            command,
            raw_key=f"correct:{command.command_id}",
            actor=CommandActor(subject_id=scenario.actor_subject_id),
            uow_factory=_uow_factory(pg_engine),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 1
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(TransactionReversalRecord))
            == 0
        )


def test_second_projection_failure_rolls_back_both_correction_events(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    command_id = uuid4()
    replacement = _post_command(
        scenario,
        command_id=command_id,
        transaction_id=uuid4(),
        debit_amount="20.00",
        credit_amount="20.00",
    )
    command = CorrectTransactionCommand(
        book_id=scenario.book_id,
        command_id=command_id,
        reverses_transaction_id=scenario.transaction_id,
        reversal_transaction_id=uuid4(),
        expected_reversal_stream_version=0,
        reason_code=ReversalReasonCode.USER_CORRECTION,
        reversal_effective_at=EFFECTIVE_AT + timedelta(minutes=1),
        replacement=CorrectionReplacement(
            transaction_id=replacement.transaction_id,
            expected_stream_version=0,
            kind=replacement.kind,
            postings=replacement.postings,
            effective_at=EFFECTIVE_AT + timedelta(minutes=2),
        ),
    )

    class FailOnReplacement(SynchronousProjector):
        def apply_stored(self, session, stored):
            if (
                stored.event_type == "JournalTransactionPosted"
                and stored.book_position == 3
            ):
                raise RuntimeError("injected replacement projection failure")
            return super().apply_stored(session, stored)

    with pytest.raises(RuntimeError, match="injected replacement"):
        execute_correct_transaction(
            command,
            raw_key=f"correct:{command.command_id}",
            actor=CommandActor(subject_id=scenario.actor_subject_id),
            uow_factory=_uow_factory(pg_engine),
            ledger_committer=LedgerCommitter(projector=FailOnReplacement()),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 1
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(TransactionReversalRecord))
            == 0
        )
        balances = {
            row.account_id: int(row.balance_units)
            for row in session.scalars(select(AccountBalanceRecord))
        }
        assert balances == {
            scenario.debit_account_id: 1234,
            scenario.credit_account_id: -1234,
        }
