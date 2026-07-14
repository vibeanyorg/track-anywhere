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
from track_anywhere.application.journal.assign_reporting_lines import (
    AssignReportingLinesCommand,
    ReportingAllocationExceeded,
    ReportingLineInput,
    UnsupportedReportingDimension,
    execute_assign_reporting_lines,
)
from track_anywhere.application.journal.clear_reporting_lines import (
    ClearReportingLinesCommand,
    execute_clear_reporting_lines,
)
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLineKind,
)
from track_anywhere.infrastructure.db.event_store import StreamVersionConflict
from track_anywhere.infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    CommandReceiptRecord,
    LedgerEventRecord,
)
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    ReportingLineRecord,
)
from track_anywhere.infrastructure.db.repositories.catalogs import CatalogNotFound
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


EFFECTIVE_AT = datetime(2026, 7, 14, 16, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _post_original(engine, scenario: JournalScenario) -> None:
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
        ),
        raw_key=f"post:{scenario.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _seed_category(engine, scenario: JournalScenario):
    category_id = uuid4()
    category_version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into categories ("
                "book_id, category_id, parent_category_id, current_name, "
                "current_version_id, status"
                ") values (:book_id, :category_id, null, 'Dining', null, 'active')"
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions ("
                "book_id, category_id, category_version_id, parent_category_id, "
                "name, status, change_reason_code"
                ") values ("
                ":book_id, :category_id, :version_id, null, "
                "'Dining', 'active', 'created'"
                ")"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
            },
        )
        connection.execute(
            text(
                "update categories set current_version_id = :version_id "
                "where book_id = :book_id and category_id = :category_id"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
            },
        )
    return category_id, category_version_id


def _line(
    category_id,
    category_version_id,
    *,
    units: str = "1000",
) -> ReportingLineInput:
    return ReportingLineInput(
        line_id=uuid4(),
        line_version_id=uuid4(),
        catalog_id=category_version_id,
        asset_code="USD",
        units=units,
        line_kind=ReportingLineKind.EXPENSE,
        dimension=ReportingDimension.CATEGORY,
        dimension_id=category_id,
    )


def _assign(
    engine,
    scenario,
    *,
    expected_revision: int,
    lines,
    command_id=None,
):
    command = AssignReportingLinesCommand(
        book_id=scenario.book_id,
        command_id=command_id or uuid4(),
        transaction_id=scenario.transaction_id,
        expected_revision=expected_revision,
        lines=tuple(lines),
        effective_at=EFFECTIVE_AT + timedelta(minutes=expected_revision + 1),
    )
    return execute_assign_reporting_lines(
        command,
        raw_key=f"assign:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(engine),
    )


def _balances(session):
    return tuple(
        sorted(
            (
                row.account_id,
                row.asset_code,
                int(row.balance_units),
                row.as_of_position,
            )
            for row in session.scalars(select(AccountBalanceRecord))
        )
    )


def test_assignment_starts_at_revision_one_and_replaces_the_full_current_set(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id, version_id = _seed_category(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    with Session(pg_engine) as session:
        balances_before = _balances(session)

    first = _assign(
        pg_engine,
        scenario,
        expected_revision=0,
        lines=(_line(category_id, version_id),),
    )
    replacement_line = _line(category_id, version_id, units="500")
    second = _assign(
        pg_engine,
        scenario,
        expected_revision=1,
        lines=(replacement_line,),
    )

    assert first.result.body["classification_revision"] == 1
    assert second.result.body == {
        "transaction_id": str(scenario.transaction_id),
        "classification_revision": 2,
        "as_of_book_position": 3,
    }
    with Session(pg_engine) as session:
        rows = tuple(session.scalars(select(ReportingLineRecord)))
        assert len(rows) == 1
        assert (
            rows[0].classification_revision,
            rows[0].line_id,
            rows[0].line_version_id,
            rows[0].catalog_id,
            rows[0].dimension_id,
            int(rows[0].units),
        ) == (
            2,
            replacement_line.line_id,
            replacement_line.line_version_id,
            version_id,
            category_id,
            500,
        )
        assert _balances(session) == balances_before


def test_stale_expected_revision_conflicts_without_replacing_current_lines(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id, version_id = _seed_category(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    original_line = _line(category_id, version_id)
    _assign(
        pg_engine,
        scenario,
        expected_revision=0,
        lines=(original_line,),
    )

    with pytest.raises(StreamVersionConflict):
        _assign(
            pg_engine,
            scenario,
            expected_revision=0,
            lines=(_line(category_id, version_id, units="500"),),
        )

    with Session(pg_engine) as session:
        row = session.scalar(select(ReportingLineRecord))
        assert row is not None and row.line_id == original_line.line_id
        assert session.scalar(select(func.count()).select_from(LedgerEventRecord)) == 2


def test_clear_is_an_explicit_revision_and_leaves_balances_byte_for_byte_unchanged(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id, version_id = _seed_category(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    _assign(
        pg_engine,
        scenario,
        expected_revision=0,
        lines=(_line(category_id, version_id),),
    )
    with Session(pg_engine) as session:
        balances_before = _balances(session)
    command = ClearReportingLinesCommand(
        book_id=scenario.book_id,
        command_id=uuid4(),
        transaction_id=scenario.transaction_id,
        expected_revision=1,
        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
    )

    outcome = execute_clear_reporting_lines(
        command,
        raw_key=f"clear:{command.command_id}",
        actor=CommandActor(subject_id=scenario.actor_subject_id),
        uow_factory=_uow_factory(pg_engine),
    )

    assert outcome.result.body["classification_revision"] == 2
    with Session(pg_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(ReportingLineRecord)) == 0
        )
        assert _balances(session) == balances_before
        event = session.scalar(
            select(LedgerEventRecord).where(
                LedgerEventRecord.event_type == "ReportingLinesCleared"
            )
        )
        assert event is not None and event.payload["classification_revision"] == 2


def test_rejects_over_allocation_before_event_or_receipt_commit(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id, version_id = _seed_category(pg_engine, scenario)
    _post_original(pg_engine, scenario)

    with pytest.raises(ReportingAllocationExceeded):
        _assign(
            pg_engine,
            scenario,
            expected_revision=0,
            lines=(_line(category_id, version_id, units="1001"),),
        )

    with Session(pg_engine) as session:
        head = session.get(BookEventHeadRecord, scenario.book_id)
        assert head is not None and head.last_position == 1
        assert (
            session.scalar(select(func.count()).select_from(CommandReceiptRecord)) == 1
        )


def test_category_dimension_requires_the_exact_book_scoped_immutable_version(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id, _ = _seed_category(pg_engine, scenario)
    _post_original(pg_engine, scenario)

    with pytest.raises(CatalogNotFound):
        _assign(
            pg_engine,
            scenario,
            expected_revision=0,
            lines=(_line(category_id, uuid4()),),
        )


def test_unbacked_reporting_dimensions_fail_closed(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id, version_id = _seed_category(pg_engine, scenario)
    _post_original(pg_engine, scenario)
    line = replace(
        _line(category_id, version_id),
        dimension=ReportingDimension.PROJECT,
    )

    with pytest.raises(UnsupportedReportingDimension):
        _assign(
            pg_engine,
            scenario,
            expected_revision=0,
            lines=(line,),
        )
