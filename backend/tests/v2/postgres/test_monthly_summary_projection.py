from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.monthly import (
    post_classified_expense,
    seed_monthly_scenario,
)
from track_anywhere.infrastructure.projections.monthly_summary import (
    cold_replay_monthly_summary,
    read_monthly_summary,
)
from track_anywhere.infrastructure.projections.worker import AsyncProjectionWorker
from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.application.ledger_committer import BookWritePaused, LedgerCommitter
from track_anywhere.domain.journal.events import (
    JournalPostingFact,
    JournalTransactionPosted,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLineKind,
    ReportingLinesAssigned,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.catalog import BookRecord
from track_anywhere.observability.audit import LedgerIntegrityAuditor
from track_anywhere.observability.metrics import LedgerMetrics
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


def test_online_monthly_summary_matches_cold_replay_and_emits_lag_metrics(
    pg_engine,
) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:monthly-metrics")
    for month, amount in ((1, "2.00"), (7, "5.00"), (1, "3.00")):
        post_classified_expense(
            pg_engine,
            scenario,
            effective_at=datetime(2026, month, 10, tzinfo=UTC),
            amount=amount,
        )
    metrics = LedgerMetrics()
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    worker = AsyncProjectionWorker(factory, metrics=metrics)
    while worker.run_once(scenario.journal.book_id).processed_events:
        pass

    with Session(pg_engine) as session:
        cold = cold_replay_monthly_summary(session, scenario.journal.book_id)
    snapshot = metrics.snapshot()
    assert cold
    assert snapshot.counters["projection.events_processed"] == 6
    assert snapshot.gauges["projection.lag"] == 0
    assert snapshot.counters["projection.dirty_periods"] >= 2


def test_non_card_refund_registry_events_project_as_negative_and_match_cold_replay(
    pg_engine,
) -> None:
    scenario = seed_monthly_scenario(
        pg_engine,
        actor_id="human:monthly-non-card-refund",
    )
    original_transaction_id = post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 1, 10, tzinfo=UTC),
        amount="10.00",
    )
    refund_transaction_id = uuid4()
    command_id = uuid4()
    refund_event_id = uuid4()
    reporting_event_id = uuid4()
    effective_at = datetime(2026, 2, 5, tzinfo=UTC)
    refund = JournalTransactionPosted(
        transaction_id=refund_transaction_id,
        kind=TransactionKind.REFUND,
        original_transaction_id=original_transaction_id,
        postings=(
            JournalPostingFact(
                posting_id=uuid4(),
                position=0,
                account_id=scenario.journal.credit_account_id,
                asset_code="USD",
                side=PostingSide.DEBIT,
                units="400",
            ),
            JournalPostingFact(
                posting_id=uuid4(),
                position=1,
                account_id=scenario.journal.debit_account_id,
                asset_code="USD",
                side=PostingSide.CREDIT,
                units="400",
            ),
        ),
    )
    reporting = ReportingLinesAssigned(
        transaction_id=refund_transaction_id,
        classification_revision=1,
        lines=(
            ReportingLine(
                line_id=uuid4(),
                line_version_id=uuid4(),
                catalog_id=scenario.category_version_id,
                position=0,
                asset_code="USD",
                units="400",
                line_kind=ReportingLineKind.EXPENSE,
                dimension=ReportingDimension.CATEGORY,
                dimension_id=scenario.category_id,
            ),
        ),
    )
    for payload in (refund, reporting):
        dumped = PRODUCTION_EVENT_REGISTRY.dump_registered(payload)
        assert (
            PRODUCTION_EVENT_REGISTRY.validate_stored(
                type(payload).event_type,
                type(payload).schema_version,
                dumped,
            )
            == payload
        )

    pending = (
        PendingEvent(
            event_id=refund_event_id,
            stream_type="journal_transaction",
            stream_id=refund_transaction_id,
            payload=refund,
            command_id=command_id,
            actor_subject_id=scenario.journal.actor_subject_id,
            correlation_id=command_id,
            causation_event_id=None,
            effective_at=effective_at,
        ),
        PendingEvent(
            event_id=reporting_event_id,
            stream_type="reporting_lines",
            stream_id=refund_transaction_id,
            payload=reporting,
            command_id=command_id,
            actor_subject_id=scenario.journal.actor_subject_id,
            correlation_id=command_id,
            causation_event_id=refund_event_id,
            effective_at=effective_at,
        ),
    )
    with Session(pg_engine) as session, session.begin():
        committer = LedgerCommitter()
        locked = committer.execute_under_book_lock(
            session,
            scenario.journal.book_id,
        )
        committer.append_and_project(
            session,
            locked_head=locked,
            expected_stream_versions={
                event.stream_key: 0 for event in pending
            },
            events=pending,
        )

    worker = AsyncProjectionWorker(
        sessionmaker(pg_engine, expire_on_commit=False)
    )
    while worker.run_once(scenario.journal.book_id).processed_events:
        pass

    periods = (date(2026, 1, 1), date(2026, 2, 1))
    with Session(pg_engine) as session:
        stored = tuple(
            session.scalars(
                select(LedgerEventRecord)
                .where(
                    LedgerEventRecord.event_id.in_(
                        (refund_event_id, reporting_event_id)
                    )
                )
                .order_by(LedgerEventRecord.book_position)
            )
        )
        assert tuple(
            PRODUCTION_EVENT_REGISTRY.validate_stored(
                record.event_type,
                record.event_schema_version,
                record.payload,
            )
            for record in stored
        ) == (refund, reporting)
        cold = cold_replay_monthly_summary(
            session,
            scenario.journal.book_id,
        )
        online = {
            period: read_monthly_summary(
                session,
                scenario.journal.book_id,
                period_start=period,
            )
            for period in periods
        }

    assert online == {period: cold[period] for period in periods}
    assert online[date(2026, 1, 1)][0].units == 1000
    assert online[date(2026, 2, 1)][0].units == -400


def test_monthly_summary_uses_utc_periods_in_non_utc_database_session(
    pg_engine,
) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:monthly-utc-period")
    post_classified_expense(
        pg_engine,
        scenario,
        # This is February 1 in Pacific/Auckland but January 31 in UTC.
        effective_at=datetime(2026, 1, 31, 12, 30, tzinfo=UTC),
        amount="5.00",
    )

    with pg_engine.connect() as connection:
        connection.execute(text("set time zone 'Pacific/Auckland'"))
        connection.commit()
        factory = sessionmaker(connection, expire_on_commit=False)
        metrics = LedgerMetrics()
        worker = AsyncProjectionWorker(factory, metrics=metrics)
        while worker.run_once(scenario.journal.book_id).processed_events:
            pass

        with Session(connection) as session:
            january = read_monthly_summary(
                session,
                scenario.journal.book_id,
                period_start=datetime(2026, 1, 1, tzinfo=UTC).date(),
            )
            february = read_monthly_summary(
                session,
                scenario.journal.book_id,
                period_start=datetime(2026, 2, 1, tzinfo=UTC).date(),
            )
            cold = cold_replay_monthly_summary(session, scenario.journal.book_id)

    assert january
    assert february == ()
    assert tuple(cold) == (datetime(2026, 1, 1, tzinfo=UTC).date(),)
    assert january == cold[datetime(2026, 1, 1, tzinfo=UTC).date()]
    assert metrics.snapshot().counters["projection.dirty_periods"] == 1


def test_terminal_hash_mismatch_emits_p0_and_pauses_financial_writes(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:hash-audit")
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 7, 10, tzinfo=UTC),
        amount="5.00",
    )
    signals = []
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    result = LedgerIntegrityAuditor(factory, emit=signals.append).audit_book(
        scenario.journal.book_id,
        trusted_terminal_hash=b"x" * 32,
    )

    assert result.terminal_hash_ok is False
    assert result.balance_parity_ok is True
    assert [signal.code for signal in signals] == ["terminal_hash_mismatch"]
    assert "x" * 32 not in repr(signals)
    with Session(pg_engine) as session:
        assert session.get(BookRecord, scenario.journal.book_id).write_state == (
            "paused_integrity"
        )
    with Session(pg_engine) as session, session.begin():
        with pytest.raises(BookWritePaused):
            LedgerCommitter().execute_under_book_lock(
                session,
                scenario.journal.book_id,
            )


def test_balance_projection_mismatch_emits_p0_and_pauses_book(pg_engine) -> None:
    scenario = seed_monthly_scenario(pg_engine, actor_id="human:balance-audit")
    post_classified_expense(
        pg_engine,
        scenario,
        effective_at=datetime(2026, 7, 10, tzinfo=UTC),
        amount="5.00",
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update account_balances set balance_units = balance_units + 1 "
                "where book_id = :book_id and account_id = :account_id"
            ),
            {
                "book_id": scenario.journal.book_id,
                "account_id": scenario.journal.debit_account_id,
            },
        )

    signals = []
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    result = LedgerIntegrityAuditor(factory, emit=signals.append).audit_book(
        scenario.journal.book_id
    )

    assert result.terminal_hash_ok is True
    assert result.balance_parity_ok is False
    assert [signal.code for signal in signals] == ["balance_projection_mismatch"]
    with Session(pg_engine) as session:
        assert session.get(BookRecord, scenario.journal.book_id).write_state == (
            "paused_integrity"
        )
