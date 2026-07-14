from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
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
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.reporting.events import ReportingDimension, ReportingLineKind
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class MonthlyScenario:
    journal: JournalScenario
    category_id: UUID
    category_version_id: UUID

    @property
    def actor(self) -> CommandActor:
        return CommandActor(subject_id=self.journal.actor_subject_id)


def seed_monthly_scenario(
    engine, *, actor_id: str = "human:monthly"
) -> MonthlyScenario:
    base = JournalScenario.create()
    journal = JournalScenario(
        book_id=base.book_id,
        debit_account_id=base.debit_account_id,
        credit_account_id=base.credit_account_id,
        transaction_id=base.transaction_id,
        event_id=base.event_id,
        command_id=base.command_id,
        debit_posting_id=base.debit_posting_id,
        credit_posting_id=base.credit_posting_id,
        actor_subject_id=actor_id,
    )
    seed_journal_scenario(engine, journal)
    category_id = uuid4()
    version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into categories "
                "(book_id, category_id, current_name, status) "
                "values (:book_id, :category_id, 'Food', 'active')"
            ),
            {"book_id": journal.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions "
                "(book_id, category_id, category_version_id, name, status, change_reason_code) "
                "values (:book_id, :category_id, :version_id, 'Food', 'active', 'created')"
            ),
            {
                "book_id": journal.book_id,
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
                "book_id": journal.book_id,
                "category_id": category_id,
                "version_id": version_id,
            },
        )
    return MonthlyScenario(journal, category_id, version_id)


def post_classified_expense(
    engine,
    scenario: MonthlyScenario,
    *,
    effective_at: datetime,
    amount: str,
) -> UUID:
    factory = sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(factory)

    transaction_id = uuid4()
    post = execute_post_transaction(
        PostTransactionCommand(
            book_id=scenario.journal.book_id,
            command_id=uuid4(),
            transaction_id=transaction_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=(
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.journal.debit_account_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount=amount,
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.journal.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount=amount,
                ),
            ),
            effective_at=effective_at,
        ),
        raw_key=f"monthly-post:{transaction_id}",
        actor=scenario.actor,
        uow_factory=uow_factory,
    )
    units = str(int(Decimal(amount) * 100))
    execute_assign_reporting_lines(
        AssignReportingLinesCommand(
            book_id=scenario.journal.book_id,
            command_id=uuid4(),
            transaction_id=transaction_id,
            expected_revision=0,
            lines=(
                ReportingLineInput(
                    line_id=uuid4(),
                    line_version_id=uuid4(),
                    catalog_id=scenario.category_version_id,
                    asset_code="USD",
                    units=units,
                    line_kind=ReportingLineKind.EXPENSE,
                    dimension=ReportingDimension.CATEGORY,
                    dimension_id=scenario.category_id,
                ),
            ),
            effective_at=effective_at,
        ),
        raw_key=f"monthly-classify:{transaction_id}",
        actor=scenario.actor,
        uow_factory=uow_factory,
    )
    assert post.result.last_book_position is not None
    return transaction_id


__all__ = ["MonthlyScenario", "post_classified_expense", "seed_monthly_scenario"]
