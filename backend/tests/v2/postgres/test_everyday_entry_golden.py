from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.everyday_entries import (
    ACTOR_ID,
    BOC_DEBIT_ID,
    BOOK_ID,
    DRINK_ID,
    EXPENSE_CLEARING_ID,
    HOUSEHOLD_ID,
    ICBC_CARD_ID,
    ICBC_DEBIT_ID,
    TAKEAWAY_ID,
    WALLET_ID,
    GoldenEntryScenario,
    golden_scenarios,
    money,
    seed_golden_book,
)
from track_anywhere.application.entries.commit import EntryCommitRuntime, commit_entry
from track_anywhere.application.entries.contracts import (
    AccountRef,
    CategoryRef,
    CommitEntryInput,
    ExpenseEntryInput,
    PreparedEntryStatus,
    RefundEntryInput,
)
from track_anywhere.application.entries.prepare import (
    EntryPreparationRuntime,
    prepare_entry,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.application.privacy.service import ProtectedContentService
from track_anywhere.infrastructure.crypto import (
    DuplicateDetectionKeyProvider,
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.credit_cards import (
    CreditCardTransactionRecord,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
)
from track_anywhere.infrastructure.db.repositories.privacy import (
    ProtectedContentRepository,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.infrastructure.projections.monthly_summary import (
    cold_replay_monthly_summary,
    read_monthly_summary,
)
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.queries.everyday_entries import get_everyday_entry
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


def _runtimes(pg_engine):
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)
    actor = CommandActor(ACTOR_ID)
    protected_service = ProtectedContentService(
        cipher=ProtectedContentCipher(
            ProtectedContentKeyring.from_mapping(
                active_key_ref="golden-v1",
                keys={"golden-v1": b"p" * 32},
            )
        ),
        repository=ProtectedContentRepository(),
    )
    return (
        EntryPreparationRuntime(
            actor=actor,
            uow_factory=uow_factory,
            protected_content_service=protected_service,
            duplicate_key_provider=DuplicateDetectionKeyProvider(bytes(range(32))),
        ),
        EntryCommitRuntime(
            actor=actor,
            uow_factory=uow_factory,
            ledger_committer=LedgerCommitter(),
            protected_content_service=protected_service,
        ),
        factory,
    )


def _prepare_commit(
    entry,
    *,
    prepare_runtime: EntryPreparationRuntime,
    commit_runtime: EntryCommitRuntime,
):
    prepared = prepare_entry(
        book_id=BOOK_ID,
        entry=entry,
        runtime=prepare_runtime,
    )
    assert prepared.status is PreparedEntryStatus.READY
    assert prepared.commit_token is not None
    request_id = uuid4()
    committed = commit_entry(
        book_id=BOOK_ID,
        command=CommitEntryInput(
            intent_id=prepared.intent_id,
            commit_token=prepared.commit_token,
            request_id=request_id,
        ),
        runtime=commit_runtime,
    )
    return prepared, committed, request_id


def _event_records(session: Session, request_id: UUID) -> tuple[LedgerEventRecord, ...]:
    return tuple(
        session.scalars(
            select(LedgerEventRecord)
            .where(
                LedgerEventRecord.book_id == BOOK_ID,
                LedgerEventRecord.command_id == request_id,
            )
            .order_by(LedgerEventRecord.book_position)
        )
    )


def _assert_committed_scenario(
    session: Session,
    scenario: GoldenEntryScenario,
    *,
    transaction_id: UUID,
    request_id: UUID,
) -> None:
    records = _event_records(session, request_id)
    assert tuple(record.event_type for record in records) == (
        ("JournalTransactionPosted", "ReportingLinesAssigned")
        if scenario.expected_categories
        and scenario.expected_financial_kind == "standard"
        else (
            ("CreditCardTransactionRecorded", "ReportingLinesAssigned")
            if scenario.expected_categories
            else ("CreditCardTransactionRecorded",)
        )
    )
    assert tuple(record.book_position for record in records) == tuple(
        range(records[0].book_position, records[0].book_position + len(records))
    )
    if len(records) == 2:
        assert records[1].causation_event_id == records[0].event_id
    for record in records:
        PRODUCTION_EVENT_REGISTRY.validate_stored(
            record.event_type,
            record.event_schema_version,
            record.payload,
        )

    transaction = session.get(
        JournalTransactionRecord,
        (BOOK_ID, transaction_id),
    )
    assert transaction is not None
    assert transaction.transaction_kind == scenario.expected_financial_kind
    postings = tuple(
        session.scalars(
            select(JournalPostingRecord)
            .where(
                JournalPostingRecord.book_id == BOOK_ID,
                JournalPostingRecord.transaction_id == transaction_id,
            )
            .order_by(JournalPostingRecord.posting_position)
        )
    )
    assert tuple((row.account_id, row.side) for row in postings) == (
        scenario.expected_postings
    )
    assert {int(row.units) for row in postings} == {scenario.expected_units}

    reporting = tuple(
        session.scalars(
            select(ReportingLineRecord)
            .where(
                ReportingLineRecord.book_id == BOOK_ID,
                ReportingLineRecord.transaction_id == transaction_id,
            )
            .order_by(ReportingLineRecord.line_position)
        )
    )
    assert tuple(row.dimension_id for row in reporting) == (
        scenario.expected_categories
    )
    assert tuple(int(row.units) for row in reporting) == (
        scenario.expected_reporting_units
    )
    card = session.get(CreditCardTransactionRecord, (BOOK_ID, transaction_id))
    if scenario.expected_financial_kind.startswith("credit_card_"):
        assert card is not None
        assert card.intent == scenario.expected_financial_kind.removeprefix(
            "credit_card_"
        )
    else:
        assert card is None

    view = get_everyday_entry(session, BOOK_ID, transaction_id)
    assert view.amount is not None
    assert view.amount.value == scenario.expected_value
    assert tuple(
        allocation.category_id for allocation in view.category_allocations
    ) == scenario.expected_categories
    if scenario.expected_financial_kind == "credit_card_payment":
        assert view.category_allocations == ()
        assert view.source_account is not None
        assert view.source_account.account_id == ICBC_DEBIT_ID
        assert view.target_account is not None
        assert view.target_account.account_id == ICBC_CARD_ID
        assert view.payment_account is None


def test_pg17_golden_prepare_commit_events_projections_refunds_and_monthly_signs(
    pg_engine,
) -> None:
    seed_golden_book(pg_engine)
    prepare_runtime, commit_runtime, factory = _runtimes(pg_engine)
    committed_by_name: dict[str, UUID] = {}

    for scenario in golden_scenarios():
        _, committed, request_id = _prepare_commit(
            scenario.entry,
            prepare_runtime=prepare_runtime,
            commit_runtime=commit_runtime,
        )
        committed_by_name[scenario.name] = committed.transaction_id
        with Session(pg_engine) as session:
            _assert_committed_scenario(
                session,
                scenario,
                transaction_id=committed.transaction_id,
                request_id=request_id,
            )

    august = datetime(2026, 8, 2, 8, tzinfo=UTC)
    _, full_refund, full_request_id = _prepare_commit(
        RefundEntryInput(
            original_transaction_id=committed_by_name["takeaway_wallet_53"],
            amount=None,
            occurred_at=august,
        ),
        prepare_runtime=prepare_runtime,
        commit_runtime=commit_runtime,
    )
    _, partial_card_refund, partial_request_id = _prepare_commit(
        RefundEntryInput(
            original_transaction_id=committed_by_name[
                "credit_card_charge_19_60"
            ],
            amount=money("4.00"),
            occurred_at=august + timedelta(hours=1),
        ),
        prepare_runtime=prepare_runtime,
        commit_runtime=commit_runtime,
    )

    with Session(pg_engine) as session:
        full = session.get(
            JournalTransactionRecord,
            (BOOK_ID, full_refund.transaction_id),
        )
        partial = session.get(
            CreditCardTransactionRecord,
            (BOOK_ID, partial_card_refund.transaction_id),
        )
        assert full is not None
        assert full.transaction_kind == "refund"
        full_event = PRODUCTION_EVENT_REGISTRY.validate_stored(
            _event_records(session, full_request_id)[0].event_type,
            _event_records(session, full_request_id)[0].event_schema_version,
            _event_records(session, full_request_id)[0].payload,
        )
        assert full_event.original_transaction_id == committed_by_name[
            "takeaway_wallet_53"
        ]
        assert partial is not None
        assert partial.intent == "refund"
        assert partial.original_transaction_id == committed_by_name[
            "credit_card_charge_19_60"
        ]
        assert int(partial.units) == 400
        assert len(_event_records(session, partial_request_id)) == 2

        full_view = get_everyday_entry(session, BOOK_ID, full_refund.transaction_id)
        partial_view = get_everyday_entry(
            session,
            BOOK_ID,
            partial_card_refund.transaction_id,
        )
        assert full_view.original_transaction_id == committed_by_name[
            "takeaway_wallet_53"
        ]
        assert partial_view.original_transaction_id == committed_by_name[
            "credit_card_charge_19_60"
        ]

    worker = AsyncProjectionWorker(factory)
    while worker.run_once(BOOK_ID).processed_events:
        pass
    with Session(pg_engine) as session:
        online_july = read_monthly_summary(
            session,
            BOOK_ID,
            period_start=date(2026, 7, 1),
        )
        online_august = read_monthly_summary(
            session,
            BOOK_ID,
            period_start=date(2026, 8, 1),
        )
        cold = cold_replay_monthly_summary(session, BOOK_ID)
        assert online_july == cold[date(2026, 7, 1)]
        assert online_august == cold[date(2026, 8, 1)]
        july = {row.category_id: row.units for row in online_july}
        august_totals = {row.category_id: row.units for row in online_august}
        assert july == {
            TAKEAWAY_ID: 13_260,
            DRINK_ID: 70_660,
            HOUSEHOLD_ID: 405,
        }
        assert august_totals == {TAKEAWAY_ID: -5_700}
        assert all(row.category_id != TAKEAWAY_ID or row.units != 200_000 for row in online_july)

        wallet = session.get(AccountBalanceRecord, (BOOK_ID, WALLET_ID, "CNY"))
        bank = session.get(AccountBalanceRecord, (BOOK_ID, ICBC_DEBIT_ID, "CNY"))
        card = session.get(AccountBalanceRecord, (BOOK_ID, ICBC_CARD_ID, "CNY"))
        boc = session.get(AccountBalanceRecord, (BOOK_ID, BOC_DEBIT_ID, "CNY"))
        clearing = session.get(
            AccountBalanceRecord,
            (BOOK_ID, EXPENSE_CLEARING_ID, "CNY"),
        )
        assert wallet is not None and int(wallet.balance_units) == -76_660
        assert bank is not None and int(bank.balance_units) == -200_000
        assert card is not None and int(card.balance_units) == 198_440
        assert boc is not None and int(boc.balance_units) == -405
        assert clearing is not None and int(clearing.balance_units) == 78_625


def test_pg17_strong_soft_duplicates_and_same_name_account_ambiguity(
    pg_engine,
) -> None:
    seed_golden_book(pg_engine)
    prepare_runtime, commit_runtime, _ = _runtimes(pg_engine)
    takeaway = golden_scenarios()[0]
    _, original, _ = _prepare_commit(
        takeaway.entry,
        prepare_runtime=prepare_runtime,
        commit_runtime=commit_runtime,
    )

    strong_entry = takeaway.entry.model_copy(
        update={
            "amount": money("54"),
            "occurred_at": takeaway.entry.occurred_at + timedelta(days=1),
        }
    )
    strong = prepare_entry(
        book_id=BOOK_ID,
        entry=strong_entry,
        runtime=prepare_runtime,
    )
    assert strong.status is PreparedEntryStatus.DUPLICATE_SUSPECTED
    assert strong.commit_token is None
    assert strong.clarifications[0].choices[0].resolved_id == original.transaction_id

    ordinary = golden_scenarios()[4]
    _, ordinary_receipt, _ = _prepare_commit(
        ordinary.entry,
        prepare_runtime=prepare_runtime,
        commit_runtime=commit_runtime,
    )
    soft = prepare_entry(
        book_id=BOOK_ID,
        entry=ordinary.entry,
        runtime=prepare_runtime,
    )
    assert soft.status is PreparedEntryStatus.DUPLICATE_SUSPECTED
    assert soft.commit_token is None
    assert soft.clarifications[0].choices[0].resolved_id == (
        ordinary_receipt.transaction_id
    )

    ambiguous_entry = ExpenseEntryInput(
        amount=money("19.60"),
        source_account=AccountRef(query="工商银行"),
        category=CategoryRef(path=("食品", "外卖")),
        occurred_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    ambiguous = prepare_entry(
        book_id=BOOK_ID,
        entry=ambiguous_entry,
        runtime=prepare_runtime,
    )
    assert ambiguous.status is PreparedEntryStatus.NEEDS_CLARIFICATION
    assert ambiguous.commit_token is None
    assert {
        choice.resolved_id
        for choice in ambiguous.clarifications[0].choices
    } == {ICBC_DEBIT_ID, ICBC_CARD_ID}

    resolved = prepare_entry(
        book_id=BOOK_ID,
        entry=ambiguous_entry.model_copy(
            update={
                "source_account": AccountRef(
                    query="工商银行",
                    subtype="credit_card",
                )
            }
        ),
        runtime=prepare_runtime,
    )
    assert resolved.status is PreparedEntryStatus.READY
    assert resolved.resolved.source_account_id == ICBC_CARD_ID

    resolved_by_last4 = prepare_entry(
        book_id=BOOK_ID,
        entry=ambiguous_entry.model_copy(
            update={
                "source_account": AccountRef(
                    query="工商银行",
                    last4="1242",
                )
            }
        ),
        runtime=prepare_runtime,
    )
    assert resolved_by_last4.status is PreparedEntryStatus.READY
    assert resolved_by_last4.resolved.source_account_id == ICBC_CARD_ID
