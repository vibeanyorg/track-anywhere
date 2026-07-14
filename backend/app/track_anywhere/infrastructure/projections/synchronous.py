from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...domain.journal.events import (
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ...domain.journal.models import PostingSide
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.event_store import LedgerEventRecord
from ..db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    SynchronousProjectionAppliedEventRecord,
    SynchronousProjectionEventTypeRecord,
    TransactionExternalReferenceRecord,
    TransactionReversalRecord,
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
        if type(payload) is JournalTransactionReversed:
            return SynchronousProjector._apply_journal_reversed
        raise SynchronousProjectionError(
            "registered synchronous event has no projection applier"
        )

    @staticmethod
    def _apply_journal_posted(
        session: Session,
        stored: LedgerEventRecord,
        payload: JournalTransactionPosted,
    ) -> None:
        SynchronousProjector._apply_financial_transaction(
            session,
            stored,
            transaction_id=payload.transaction_id,
            transaction_kind=payload.kind.value,
            description_ref=payload.description_ref,
            postings=payload.postings,
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

    @staticmethod
    def _apply_journal_reversed(
        session: Session,
        stored: LedgerEventRecord,
        payload: JournalTransactionReversed,
    ) -> None:
        original_transaction = session.get(
            JournalTransactionRecord,
            (stored.book_id, payload.reverses_transaction_id),
        )
        if original_transaction is None:
            raise SynchronousProjectionError(
                "reversal target is not projected in the same Book"
            )
        if (
            original_transaction.source_event_id != payload.original_event_id
            or original_transaction.source_position >= stored.book_position
        ):
            raise SynchronousProjectionError(
                "reversal target does not precede its compensation event"
            )
        original_event = session.get(LedgerEventRecord, payload.original_event_id)
        if (
            original_event is None
            or original_event.book_id != stored.book_id
            or original_event.event_hash.hex() != payload.original_event_hash
        ):
            raise SynchronousProjectionError(
                "reversal provenance does not match the immutable source event"
            )
        original_payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            original_event.event_type,
            original_event.event_schema_version,
            original_event.payload,
        )
        if type(original_payload) is JournalTransactionPosted:
            if original_payload.transaction_id != payload.reverses_transaction_id:
                raise SynchronousProjectionError(
                    "reversal source transaction identity does not match"
                )
            original_postings = original_payload.postings
        elif type(original_payload) is JournalTransactionReversed:
            if (
                original_payload.reversal_transaction_id
                != payload.reverses_transaction_id
            ):
                raise SynchronousProjectionError(
                    "reversal source transaction identity does not match"
                )
            original_postings = original_payload.inverse_postings
        else:
            raise SynchronousProjectionError(
                "reversal source is not a financial transaction event"
            )
        SynchronousProjector._validate_exact_inverse(
            original_postings,
            payload.inverse_postings,
        )
        SynchronousProjector._apply_financial_transaction(
            session,
            stored,
            transaction_id=payload.reversal_transaction_id,
            transaction_kind=original_transaction.transaction_kind,
            description_ref=payload.description_ref,
            postings=payload.inverse_postings,
        )
        session.add(
            TransactionReversalRecord(
                book_id=stored.book_id,
                reversal_transaction_id=payload.reversal_transaction_id,
                original_transaction_id=payload.reverses_transaction_id,
                source_event_id=stored.event_id,
                original_event_id=payload.original_event_id,
                original_event_hash=bytes.fromhex(payload.original_event_hash),
                reason_code=payload.reason_code.value,
            )
        )

    @staticmethod
    def _validate_exact_inverse(original_postings, inverse_postings) -> None:
        if len(original_postings) != len(inverse_postings):
            raise SynchronousProjectionError("reversal posting count does not match")
        if {posting.posting_id for posting in original_postings} & {
            posting.posting_id for posting in inverse_postings
        }:
            raise SynchronousProjectionError("reversal posting identities must be new")
        for original, inverse in zip(
            original_postings,
            inverse_postings,
            strict=True,
        ):
            expected_side = (
                PostingSide.CREDIT
                if original.side is PostingSide.DEBIT
                else PostingSide.DEBIT
            )
            if (
                inverse.position != original.position
                or inverse.account_id != original.account_id
                or inverse.asset_code != original.asset_code
                or inverse.units != original.units
                or inverse.side is not expected_side
            ):
                raise SynchronousProjectionError(
                    "reversal postings are not the exact source inverse"
                )

    @staticmethod
    def _apply_financial_transaction(
        session: Session,
        stored: LedgerEventRecord,
        *,
        transaction_id: UUID,
        transaction_kind: str,
        description_ref: UUID | None,
        postings,
    ) -> None:
        transaction = JournalTransactionRecord(
            book_id=stored.book_id,
            transaction_id=transaction_id,
            source_event_id=stored.event_id,
            source_position=stored.book_position,
            effective_at=stored.effective_at,
            transaction_kind=transaction_kind,
            description_ref=description_ref,
        )
        session.add(transaction)
        session.flush([transaction])

        posting_records = [
            JournalPostingRecord(
                book_id=stored.book_id,
                transaction_id=transaction_id,
                posting_id=posting.posting_id,
                posting_position=posting.position,
                account_id=posting.account_id,
                asset_code=posting.asset_code,
                side=posting.side.value,
                units=int(posting.units),
            )
            for posting in postings
        ]
        session.add_all(posting_records)
        session.flush(posting_records)

        for posting in postings:
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


__all__ = [
    "ProjectionApplyResult",
    "SynchronousProjectionError",
    "SynchronousProjector",
]
