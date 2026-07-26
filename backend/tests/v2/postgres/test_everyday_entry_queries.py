from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
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
from track_anywhere.application.journal.reverse_transaction import (
    ReverseTransactionCommand,
    execute_reverse_transaction,
)
from track_anywhere.domain.journal.events import ReversalReasonCode
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import (
    ReportingDimension,
    ReportingLineKind,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere.queries.everyday_entries import (
    EverydayEntryKind,
    get_everyday_entry,
)


WHEN = datetime(2026, 7, 24, 12, tzinfo=UTC)


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _category(
    engine,
    scenario: JournalScenario,
    *,
    name: str,
    parent_category_id: UUID | None,
) -> tuple[UUID, UUID]:
    category_id = uuid4()
    version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into categories (
                    book_id, category_id, parent_category_id, current_name,
                    current_version_id, status
                ) values (
                    :book_id, :category_id, :parent_id, :name, null, 'active'
                )
                """
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "parent_id": parent_category_id,
                "name": name,
            },
        )
        connection.execute(
            text(
                """
                insert into category_versions (
                    book_id, category_id, category_version_id,
                    parent_category_id, name, status, change_reason_code
                ) values (
                    :book_id, :category_id, :version_id,
                    :parent_id, :name, 'active', 'created'
                )
                """
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
                "parent_id": parent_category_id,
                "name": name,
            },
        )
        connection.execute(
            text(
                """
                update categories
                set current_version_id = :version_id
                where book_id = :book_id and category_id = :category_id
                """
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )
    return category_id, version_id


def test_sql_source_composes_split_category_paths_and_reversal_links(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    actor = CommandActor(subject_id=scenario.actor_subject_id)
    root_id, _ = _category(
        pg_engine,
        scenario,
        name="食品",
        parent_category_id=None,
    )
    delivery_id, delivery_version_id = _category(
        pg_engine,
        scenario,
        name="外卖",
        parent_category_id=root_id,
    )
    drinks_id, drinks_version_id = _category(
        pg_engine,
        scenario,
        name="饮料",
        parent_category_id=root_id,
    )
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
                    amount="53.00",
                ),
                PostTransactionPosting(
                    posting_id=scenario.credit_posting_id,
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="53.00",
                ),
            ),
            effective_at=WHEN,
        ),
        raw_key=f"everyday-post:{scenario.command_id}",
        actor=actor,
        uow_factory=_uow_factory(pg_engine),
    )
    execute_assign_reporting_lines(
        AssignReportingLinesCommand(
            book_id=scenario.book_id,
            command_id=uuid4(),
            transaction_id=scenario.transaction_id,
            expected_revision=0,
            lines=(
                ReportingLineInput(
                    line_id=uuid4(),
                    line_version_id=uuid4(),
                    catalog_id=delivery_version_id,
                    asset_code="USD",
                    units="3000",
                    line_kind=ReportingLineKind.EXPENSE,
                    dimension=ReportingDimension.CATEGORY,
                    dimension_id=delivery_id,
                ),
                ReportingLineInput(
                    line_id=uuid4(),
                    line_version_id=uuid4(),
                    catalog_id=drinks_version_id,
                    asset_code="USD",
                    units="2300",
                    line_kind=ReportingLineKind.EXPENSE,
                    dimension=ReportingDimension.CATEGORY,
                    dimension_id=drinks_id,
                ),
            ),
            effective_at=WHEN + timedelta(minutes=1),
        ),
        raw_key=f"everyday-classify:{scenario.transaction_id}",
        actor=actor,
        uow_factory=_uow_factory(pg_engine),
    )
    reversal_id = uuid4()
    execute_reverse_transaction(
        ReverseTransactionCommand(
            book_id=scenario.book_id,
            command_id=uuid4(),
            reversal_transaction_id=reversal_id,
            reverses_transaction_id=scenario.transaction_id,
            expected_stream_version=0,
            reason_code=ReversalReasonCode.USER_CORRECTION,
            effective_at=WHEN + timedelta(minutes=2),
        ),
        raw_key=f"everyday-reverse:{reversal_id}",
        actor=actor,
        uow_factory=_uow_factory(pg_engine),
    )

    with Session(pg_engine) as session:
        original = get_everyday_entry(
            session,
            scenario.book_id,
            scenario.transaction_id,
        )
        reversal = get_everyday_entry(session, scenario.book_id, reversal_id)

    assert original.kind is EverydayEntryKind.EXPENSE
    assert original.amount is not None and original.amount.value == "53.00"
    assert original.reversed_by_transaction_id == reversal_id
    assert [
        (allocation.path, allocation.amount.value)
        for allocation in original.category_allocations
    ] == [
        (("食品", "外卖"), "30.00"),
        (("食品", "饮料"), "23.00"),
    ]
    assert reversal.kind is EverydayEntryKind.REVERSAL
    assert reversal.reverses_transaction_id == scenario.transaction_id
    assert [
        (allocation.path, allocation.amount.value)
        for allocation in reversal.category_allocations
    ] == [
        (("食品", "外卖"), "30.00"),
        (("食品", "饮料"), "23.00"),
    ]


def test_sql_source_never_reads_a_transaction_from_another_book(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)

    with Session(pg_engine) as session, pytest.raises(
        LookupError,
        match="Book not found",
    ):
        get_everyday_entry(
            session,
            UUID("00000000-0000-4000-8000-000000000099"),
            scenario.transaction_id,
        )
