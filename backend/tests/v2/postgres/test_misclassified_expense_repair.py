from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)
from track_anywhere.application.catalogs.close_account import (
    AccountBalanceNonzero,
    CloseAccount,
    close_account,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.application.journal.post_transaction import (
    PostTransactionCommand,
    PostTransactionPosting,
    execute_post_transaction,
)
from track_anywhere.application.repairs import (
    RepairMisclassifiedExpense,
    canonical_expense_clearing_account_id,
    ensure_internal_accounts,
    execute_misclassified_expense_repair,
    repair_command_id,
    replacement_transaction_id,
    reversal_transaction_id,
)
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.domain.journal.models import AccountSystemRole
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.event_store import LedgerEventRecord
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
    TransactionExternalReferenceRecord,
    TransactionReversalRecord,
)
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


EFFECTIVE_AT = datetime(2026, 7, 24, 12, 30, tzinfo=UTC)


def test_repair_reverses_reclassifies_preserves_reference_and_replays(
    pg_engine,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    wrong_account_id = uuid4()
    category_id = uuid4()
    category_version_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update book_members "
                "set scopes='[\"book:write\",\"ledger:write\"]'::jsonb "
                "where book_id=:book_id and user_id=:user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, current_name, status"
                ") values (:book_id, :account_id, 'USD', 'expense', "
                "'Dining mistaken account', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "account_id": wrong_account_id,
            },
        )
        connection.execute(
            text(
                "insert into categories ("
                "book_id, category_id, parent_category_id, current_name, "
                "current_version_id, status) values ("
                ":book_id, :category_id, null, 'Dining', null, 'active')"
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions ("
                "book_id, category_id, category_version_id, parent_category_id, "
                "name, status, usage_kind, change_reason_code) values ("
                ":book_id, :category_id, :version_id, null, 'Dining', "
                "'active', 'expense', 'created')"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
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
                "version_id": category_version_id,
            },
        )

    factory = sessionmaker(pg_engine, expire_on_commit=False)
    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)
    actor = CommandActor(scenario.actor_subject_id)
    internal_accounts = ensure_internal_accounts(
        book_id=scenario.book_id,
        asset_codes=("USD",),
        roles=(
            AccountSystemRole.EXPENSE_CLEARING,
            AccountSystemRole.INCOME_CLEARING,
            AccountSystemRole.BALANCE_ADJUSTMENT,
        ),
        actor=actor,
        uow_factory=uow_factory,
    )
    assert len(internal_accounts) == 3
    execute_post_transaction(
        PostTransactionCommand(
            book_id=scenario.book_id,
            command_id=scenario.command_id,
            transaction_id=scenario.transaction_id,
            expected_stream_version=0,
            kind=TransactionKind.STANDARD,
            postings=(
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=wrong_account_id,
                    asset_code="USD",
                    side=PostingSide.DEBIT,
                    amount="12.34",
                ),
                PostTransactionPosting(
                    posting_id=uuid4(),
                    account_id=scenario.credit_account_id,
                    asset_code="USD",
                    side=PostingSide.CREDIT,
                    amount="12.34",
                ),
            ),
            effective_at=EFFECTIVE_AT,
            external_references=(
                FinancialExternalReference(
                    provider_code="legacy",
                    kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                    reference="legacy-expense-1",
                ),
            ),
        ),
        raw_key=f"post:{scenario.command_id}",
        actor=actor,
        uow_factory=uow_factory,
    )
    with pytest.raises(AccountBalanceNonzero):
        close_account(
            CloseAccount(
                book_id=scenario.book_id,
                account_id=wrong_account_id,
            ),
            actor=actor,
            uow_factory=uow_factory,
        )
    command = RepairMisclassifiedExpense(
        book_id=scenario.book_id,
        command_id=repair_command_id(
            scenario.book_id,
            scenario.transaction_id,
        ),
        original_transaction_id=scenario.transaction_id,
        reversal_transaction_id=reversal_transaction_id(
            scenario.book_id,
            scenario.transaction_id,
        ),
        replacement_transaction_id=replacement_transaction_id(
            scenario.book_id,
            scenario.transaction_id,
        ),
        wrong_expense_account_id=wrong_account_id,
        category_id=category_id,
    )
    first = execute_misclassified_expense_repair(
        command,
        actor=actor,
        uow_factory=uow_factory,
    )
    replay = execute_misclassified_expense_repair(
        command,
        actor=actor,
        uow_factory=uow_factory,
    )
    assert first.replayed is False
    assert replay.replayed is True

    close_account(
        CloseAccount(
            book_id=scenario.book_id,
            account_id=wrong_account_id,
        ),
        actor=actor,
        uow_factory=uow_factory,
    )

    replacement_id = command.replacement_transaction_id
    clearing_id = canonical_expense_clearing_account_id(
        scenario.book_id,
        "USD",
    )
    with Session(pg_engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(LedgerEventRecord)
            .where(LedgerEventRecord.book_id == scenario.book_id)
        ) == 4
        reversal = session.scalar(
            select(TransactionReversalRecord).where(
                TransactionReversalRecord.book_id == scenario.book_id,
                TransactionReversalRecord.original_transaction_id
                == scenario.transaction_id,
            )
        )
        assert reversal is not None
        assert (
            reversal.reversal_transaction_id
            == command.reversal_transaction_id
        )
        replacement = session.get(
            JournalTransactionRecord,
            (scenario.book_id, replacement_id),
        )
        assert replacement is not None
        assert replacement.transaction_kind == "standard"
        postings = tuple(
            session.scalars(
                select(JournalPostingRecord)
                .where(
                    JournalPostingRecord.book_id == scenario.book_id,
                    JournalPostingRecord.transaction_id == replacement_id,
                )
                .order_by(JournalPostingRecord.posting_position)
            )
        )
        assert tuple(
            (posting.account_id, posting.side, int(posting.units))
            for posting in postings
        ) == (
            (clearing_id, "debit", 1234),
            (scenario.credit_account_id, "credit", 1234),
        )
        reporting = session.scalar(
            select(ReportingLineRecord).where(
                ReportingLineRecord.book_id == scenario.book_id,
                ReportingLineRecord.transaction_id == replacement_id,
            )
        )
        assert reporting is not None
        assert reporting.dimension_id == category_id
        assert int(reporting.units) == 1234
        references = tuple(
            session.scalars(
                select(TransactionExternalReferenceRecord).where(
                    TransactionExternalReferenceRecord.book_id
                    == scenario.book_id,
                    TransactionExternalReferenceRecord.transaction_id
                    == replacement_id,
                )
            )
        )
        assert tuple(
            (
                reference.provider_code,
                reference.reference_kind,
                reference.reference_value,
            )
            for reference in references
        ) == (("legacy", "provider_transaction", "legacy-expense-1"),)
        wrong = session.get(
            AccountRecord,
            (scenario.book_id, wrong_account_id),
        )
        assert wrong is not None
        assert wrong.status == "closed"
        balances = {
            row.account_id: int(row.balance_units)
            for row in session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == scenario.book_id,
                    AccountBalanceRecord.account_id.in_(
                        (
                            wrong_account_id,
                            scenario.credit_account_id,
                            clearing_id,
                        )
                    ),
                )
            )
        }
        assert balances == {
            wrong_account_id: 0,
            scenario.credit_account_id: -1234,
            clearing_id: 1234,
        }
