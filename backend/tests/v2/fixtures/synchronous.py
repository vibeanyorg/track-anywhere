from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from track_anywhere.application.event_batch import PendingEvent
from track_anywhere.domain.journal.events import (
    ExternalReferenceKind,
    FinancialExternalReference,
    JournalPostingFact,
    JournalTransactionPosted,
)
from track_anywhere.domain.journal.models import PostingSide, TransactionKind
from track_anywhere.infrastructure.db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    SynchronousProjectionAppliedEventRecord,
    TransactionExternalReferenceRecord,
)


@dataclass(frozen=True, slots=True)
class JournalScenario:
    book_id: UUID
    debit_account_id: UUID
    credit_account_id: UUID
    transaction_id: UUID
    event_id: UUID
    command_id: UUID
    debit_posting_id: UUID
    credit_posting_id: UUID
    actor_subject_id: str = "human:sync-test"

    @classmethod
    def create(cls) -> JournalScenario:
        return cls(
            book_id=uuid4(),
            debit_account_id=uuid4(),
            credit_account_id=uuid4(),
            transaction_id=uuid4(),
            event_id=uuid4(),
            command_id=uuid4(),
            debit_posting_id=uuid4(),
            credit_posting_id=uuid4(),
        )


def seed_journal_scenario(
    engine,
    scenario: JournalScenario,
    *,
    credit_account_type: str = "asset",
    credit_account_subtype: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("""
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
            on conflict (asset_code) do nothing
            """)
        )
        connection.execute(
            text("""
            insert into books (book_id, current_name, base_asset_code, write_state)
            values (:book_id, 'Sync projection', 'USD', 'active')
            """),
            {"book_id": scenario.book_id},
        )
        connection.execute(
            text("""
            insert into book_event_heads (book_id, last_position, last_hash)
            values (:book_id, 0, :zero_hash)
            """),
            {"book_id": scenario.book_id, "zero_hash": bytes(32)},
        )
        for account_id, name, account_type, account_subtype in (
            (scenario.debit_account_id, "Debit", "asset", None),
            (
                scenario.credit_account_id,
                "Credit",
                credit_account_type,
                credit_account_subtype,
            ),
        ):
            connection.execute(
                text("""
                insert into accounts (
                    book_id, account_id, asset_code, account_type,
                    account_subtype, current_name, status
                ) values (
                    :book_id, :account_id, 'USD', :account_type,
                    :account_subtype, :name, 'active'
                )
                """),
                {
                    "book_id": scenario.book_id,
                    "account_id": account_id,
                    "name": name,
                    "account_type": account_type,
                    "account_subtype": account_subtype,
                },
            )
        connection.execute(
            text("""
            insert into users (user_id, subject_type, current_display_name, status)
            values (:user_id, 'human', 'Sync Test', 'active')
            """),
            {"user_id": scenario.actor_subject_id},
        )
        connection.execute(
            text("""
            insert into book_members (book_id, user_id, role, status, scopes)
            values (:book_id, :user_id, 'owner', 'active', '["ledger:write"]')
            """),
            {"book_id": scenario.book_id, "user_id": scenario.actor_subject_id},
        )


def posted_event(
    scenario: JournalScenario,
    *,
    debit_side: PostingSide = PostingSide.DEBIT,
    credit_side: PostingSide = PostingSide.CREDIT,
) -> JournalTransactionPosted:
    return JournalTransactionPosted(
        transaction_id=scenario.transaction_id,
        kind=TransactionKind.STANDARD,
        postings=(
            JournalPostingFact(
                posting_id=scenario.debit_posting_id,
                position=0,
                account_id=scenario.debit_account_id,
                asset_code="USD",
                side=debit_side,
                units="1000",
            ),
            JournalPostingFact(
                posting_id=scenario.credit_posting_id,
                position=1,
                account_id=scenario.credit_account_id,
                asset_code="USD",
                side=credit_side,
                units="1000",
            ),
        ),
        external_references=(
            FinancialExternalReference(
                provider_code="stripe",
                kind=ExternalReferenceKind.PROVIDER_TRANSACTION,
                reference="pi_sync_1",
            ),
        ),
    )


def pending_posted_event(
    scenario: JournalScenario,
    *,
    payload: JournalTransactionPosted | None = None,
) -> PendingEvent:
    return PendingEvent(
        event_id=scenario.event_id,
        stream_type="journal_transaction",
        stream_id=scenario.transaction_id,
        payload=payload or posted_event(scenario),
        command_id=scenario.command_id,
        actor_subject_id=scenario.actor_subject_id,
        correlation_id=scenario.command_id,
        causation_event_id=None,
        effective_at=datetime(2026, 7, 14, tzinfo=UTC),
    )


def projection_state(session: Session, scenario: JournalScenario) -> dict[str, object]:
    transaction = session.get(
        JournalTransactionRecord,
        (scenario.book_id, scenario.transaction_id),
    )
    postings = tuple(
        (
            record.posting_id,
            record.posting_position,
            record.account_id,
            record.asset_code,
            record.side,
            int(record.units),
        )
        for record in session.scalars(
            select(JournalPostingRecord)
            .where(JournalPostingRecord.book_id == scenario.book_id)
            .order_by(JournalPostingRecord.posting_position)
        )
    )
    balances = tuple(
        sorted(
            (
                record.account_id,
                record.asset_code,
                int(record.balance_units),
                record.as_of_position,
            )
            for record in session.scalars(
                select(AccountBalanceRecord).where(
                    AccountBalanceRecord.book_id == scenario.book_id
                )
            )
        )
    )
    references = tuple(
        (
            record.provider_code,
            record.reference_kind,
            record.reference_value,
            record.source_event_id,
        )
        for record in session.scalars(
            select(TransactionExternalReferenceRecord).where(
                TransactionExternalReferenceRecord.book_id == scenario.book_id
            )
        )
    )
    marker = session.get(
        SynchronousProjectionAppliedEventRecord,
        (scenario.book_id, scenario.event_id),
    )
    return {
        "transaction": None
        if transaction is None
        else (
            transaction.transaction_id,
            transaction.source_event_id,
            transaction.source_position,
            transaction.transaction_kind,
        ),
        "postings": postings,
        "balances": balances,
        "references": references,
        "projection_version": None if marker is None else marker.projection_version,
    }
