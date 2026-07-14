from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...domain.journal.events import JournalTransactionPosted
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.event_store import LedgerEventRecord
from ..db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    SynchronousProjectionAppliedEventRecord,
    SynchronousProjectionEventTypeRecord,
    TransactionExternalReferenceRecord,
)


class SynchronousProjectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectionApplyResult:
    event_id: UUID
    required: bool
    applied: bool
    projection_version: int | None


class SynchronousProjector:
    """Apply required projections in the event append transaction."""

    def apply_stored(
        self,
        session: Session,
        stored: LedgerEventRecord,
    ) -> ProjectionApplyResult:
        if type(stored) is not LedgerEventRecord:
            raise SynchronousProjectionError("stored event has an invalid runtime type")
        required = session.get(
            SynchronousProjectionEventTypeRecord,
            (stored.event_type, stored.event_schema_version),
        )
        if required is None:
            return ProjectionApplyResult(
                event_id=stored.event_id,
                required=False,
                applied=False,
                projection_version=None,
            )

        payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            stored.event_type,
            stored.event_schema_version,
            stored.payload,
        )
        applier = self._applier_for(payload)
        inserted = session.execute(
            insert(SynchronousProjectionAppliedEventRecord)
            .values(
                book_id=stored.book_id,
                event_id=stored.event_id,
                projection_version=required.projection_version,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    SynchronousProjectionAppliedEventRecord.book_id,
                    SynchronousProjectionAppliedEventRecord.event_id,
                )
            )
            .returning(SynchronousProjectionAppliedEventRecord.event_id)
        ).scalar_one_or_none()
        if inserted is None:
            return ProjectionApplyResult(
                event_id=stored.event_id,
                required=True,
                applied=False,
                projection_version=required.projection_version,
            )

        applier(session, stored, payload)
        session.flush()
        return ProjectionApplyResult(
            event_id=stored.event_id,
            required=True,
            applied=True,
            projection_version=required.projection_version,
        )

    @staticmethod
    def _applier_for(payload):
        if type(payload) is JournalTransactionPosted:
            return SynchronousProjector._apply_journal_posted
        raise SynchronousProjectionError(
            "registered synchronous event has no projection applier"
        )

    @staticmethod
    def _apply_journal_posted(
        session: Session,
        stored: LedgerEventRecord,
        payload: JournalTransactionPosted,
    ) -> None:
        transaction = JournalTransactionRecord(
            book_id=stored.book_id,
            transaction_id=payload.transaction_id,
            source_event_id=stored.event_id,
            source_position=stored.book_position,
            effective_at=stored.effective_at,
            transaction_kind=payload.kind.value,
            description_ref=payload.description_ref,
        )
        session.add(transaction)
        session.flush([transaction])

        posting_records = [
            JournalPostingRecord(
                book_id=stored.book_id,
                transaction_id=payload.transaction_id,
                posting_id=posting.posting_id,
                posting_position=posting.position,
                account_id=posting.account_id,
                asset_code=posting.asset_code,
                side=posting.side.value,
                units=int(posting.units),
            )
            for posting in payload.postings
        ]
        session.add_all(posting_records)
        session.flush(posting_records)

        for posting in payload.postings:
            signed_units = (
                int(posting.units)
                if posting.side.value == "debit"
                else -int(posting.units)
            )
            balance_insert = insert(AccountBalanceRecord).values(
                book_id=stored.book_id,
                account_id=posting.account_id,
                asset_code=posting.asset_code,
                balance_units=signed_units,
                as_of_position=stored.book_position,
            )
            session.execute(
                balance_insert.on_conflict_do_update(
                    index_elements=(
                        AccountBalanceRecord.book_id,
                        AccountBalanceRecord.account_id,
                        AccountBalanceRecord.asset_code,
                    ),
                    set_={
                        "balance_units": AccountBalanceRecord.balance_units
                        + balance_insert.excluded.balance_units,
                        "as_of_position": balance_insert.excluded.as_of_position,
                    },
                )
            )

        session.add_all(
            [
                TransactionExternalReferenceRecord(
                    book_id=stored.book_id,
                    transaction_id=payload.transaction_id,
                    provider_code=reference.provider_code,
                    reference_kind=reference.kind.value,
                    reference_value=reference.reference,
                    source_event_id=stored.event_id,
                )
                for reference in payload.external_references
            ]
        )


__all__ = [
    "ProjectionApplyResult",
    "SynchronousProjectionError",
    "SynchronousProjector",
]
