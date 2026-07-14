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
from track_anywhere.application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingLineInput,
    execute_assign_reporting_lines,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLineKind,
)
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    ReportingLineRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.serialization.event_registry import PRODUCTION_EVENT_REGISTRY


EFFECTIVE_AT = datetime(2026, 7, 14, 18, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _seed_category(engine, scenario, category_id, version_id) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into categories ("
                "book_id, category_id, current_name, current_version_id, status"
                ") values (:book_id, :category_id, 'Replay', null, 'active')"
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions ("
                "book_id, category_id, category_version_id, name, status, "
                "change_reason_code"
                ") values ("
                ":book_id, :category_id, :version_id, 'Replay', 'active', 'created'"
                ")"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )


def _state(session):
    return {
        "lines": tuple(
            (
                row.transaction_id,
                row.classification_revision,
                row.line_id,
                row.line_version_id,
                row.catalog_id,
                row.line_position,
                row.asset_code,
                int(row.units),
                row.dimension_id,
                row.source_event_id,
            )
            for row in session.scalars(
                select(ReportingLineRecord).order_by(ReportingLineRecord.line_position)
            )
        ),
        "balances": tuple(
            sorted(
                (
                    row.account_id,
                    row.asset_code,
                    int(row.balance_units),
                    row.as_of_position,
                )
                for row in session.scalars(select(AccountBalanceRecord))
            )
        ),
    }


def test_cold_replay_rebuilds_the_same_current_reporting_snapshot(
    migrated_postgres_source_target,
) -> None:
    source_db, target_db = migrated_postgres_source_target
    source = create_engine(source_db.runtime_url, pool_pre_ping=True)
    target = create_engine(target_db.runtime_url, pool_pre_ping=True)
    scenario = JournalScenario.create()
    category_id = uuid4()
    version_id = uuid4()
    actor = CommandActor(subject_id=scenario.actor_subject_id)
    try:
        for engine in (source, target):
            seed_journal_scenario(engine, scenario)
            _seed_category(engine, scenario, category_id, version_id)
        post = PostTransactionCommand(
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
        )
        execute_post_transaction(
            post,
            raw_key="replay-post",
            actor=actor,
            uow_factory=_uow_factory(source),
        )
        for revision, units in ((0, "1000"), (1, "500")):
            command = AssignReportingLinesCommand(
                book_id=scenario.book_id,
                command_id=uuid4(),
                transaction_id=scenario.transaction_id,
                expected_revision=revision,
                lines=(
                    ReportingLineInput(
                        line_id=uuid4(),
                        line_version_id=uuid4(),
                        catalog_id=version_id,
                        asset_code="USD",
                        units=units,
                        line_kind=ReportingLineKind.EXPENSE,
                        dimension=ReportingDimension.CATEGORY,
                        dimension_id=category_id,
                    ),
                ),
                effective_at=EFFECTIVE_AT + timedelta(minutes=revision + 1),
            )
            execute_assign_reporting_lines(
                command,
                raw_key=f"replay-assign:{revision}",
                actor=actor,
                uow_factory=_uow_factory(source),
            )

        with Session(source) as session:
            stored = tuple(
                session.scalars(
                    select(LedgerEventRecord).order_by(LedgerEventRecord.book_position)
                )
            )
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
                    ("journal_transaction", scenario.transaction_id): 0,
                    ("reporting_lines", scenario.transaction_id): 0,
                },
                events=pending,
            )

        with Session(source) as source_session, Session(target) as target_session:
            assert _state(source_session) == _state(target_session)
    finally:
        target.dispose()
        source.dispose()
