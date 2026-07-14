from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...domain.journal.events import (
    FinancialExternalReferenceCorrected,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ...domain.journal.models import PostingSide
from ...domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from ...domain.reporting.events import (
    ReportingDimension,
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.catalog import CategoryVersionRecord
from ..db.models.event_store import LedgerEventRecord
from ..db.models.investments import (
    InvestmentLotAllocationRecord,
    InvestmentLotRecord,
)
from ..db.models.projections import (
    AccountBalanceRecord,
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
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
        if type(payload) is ReportingLinesAssigned:
            return SynchronousProjector._apply_reporting_lines_assigned
        if type(payload) is ReportingLinesCleared:
            return SynchronousProjector._apply_reporting_lines_cleared
        if type(payload) is FinancialExternalReferenceCorrected:
            return SynchronousProjector._apply_external_reference_corrected
        if type(payload) is InvestmentLotAcquired:
            return SynchronousProjector._apply_investment_lot_acquired
        if type(payload) is InvestmentLotDisposed:
            return SynchronousProjector._apply_investment_lot_disposed
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
    def _validate_reporting_source(
        session: Session,
        stored: LedgerEventRecord,
        *,
        transaction_id: UUID,
        classification_revision: int,
    ) -> JournalTransactionRecord:
        if (
            stored.stream_type != "reporting_lines"
            or stored.stream_id != transaction_id
            or stored.stream_version != classification_revision
        ):
            raise SynchronousProjectionError(
                "reporting event identity does not match its classification revision"
            )
        transaction = session.get(
            JournalTransactionRecord,
            (stored.book_id, transaction_id),
        )
        if transaction is None:
            raise SynchronousProjectionError(
                "reporting target is not projected in the same Book"
            )
        if transaction.source_position >= stored.book_position:
            raise SynchronousProjectionError(
                "reporting target does not precede its classification event"
            )
        return transaction

    @staticmethod
    def _validate_reporting_lines(
        session: Session,
        stored: LedgerEventRecord,
        payload: ReportingLinesAssigned,
    ) -> None:
        postings = tuple(
            session.scalars(
                select(JournalPostingRecord).where(
                    JournalPostingRecord.book_id == stored.book_id,
                    JournalPostingRecord.transaction_id == payload.transaction_id,
                )
            )
        )
        if not postings:
            raise SynchronousProjectionError(
                "reporting target has no immutable journal postings"
            )
        debit_by_asset: dict[str, int] = {}
        credit_by_asset: dict[str, int] = {}
        for posting in postings:
            target = debit_by_asset if posting.side == "debit" else credit_by_asset
            target[posting.asset_code] = target.get(posting.asset_code, 0) + int(
                posting.units
            )
        if debit_by_asset != credit_by_asset:
            raise SynchronousProjectionError(
                "reporting target postings are not balanced by asset"
            )

        allocated_by_asset: dict[str, int] = {}
        for line in payload.lines:
            if (
                line.dimension is not ReportingDimension.CATEGORY
                or line.dimension_id is None
            ):
                raise SynchronousProjectionError(
                    "reporting dimension has no immutable V2 catalog contract"
                )
            category_version = session.get(
                CategoryVersionRecord,
                (stored.book_id, line.dimension_id, line.catalog_id),
            )
            if category_version is None:
                raise SynchronousProjectionError(
                    "reporting category/version pair does not exist in the Book"
                )
            allocated_by_asset[line.asset_code] = allocated_by_asset.get(
                line.asset_code, 0
            ) + int(line.units)
        for asset_code, allocated in allocated_by_asset.items():
            if allocated > debit_by_asset.get(asset_code, 0):
                raise SynchronousProjectionError(
                    "reporting allocation exceeds the transaction amount"
                )

    @staticmethod
    def _delete_current_reporting_lines(
        session: Session,
        *,
        book_id: UUID,
        transaction_id: UUID,
    ) -> None:
        session.execute(
            delete(ReportingLineRecord).where(
                ReportingLineRecord.book_id == book_id,
                ReportingLineRecord.transaction_id == transaction_id,
            )
        )

    @staticmethod
    def _apply_reporting_lines_assigned(
        session: Session,
        stored: LedgerEventRecord,
        payload: ReportingLinesAssigned,
    ) -> None:
        SynchronousProjector._validate_reporting_source(
            session,
            stored,
            transaction_id=payload.transaction_id,
            classification_revision=payload.classification_revision,
        )
        SynchronousProjector._validate_reporting_lines(session, stored, payload)
        SynchronousProjector._delete_current_reporting_lines(
            session,
            book_id=stored.book_id,
            transaction_id=payload.transaction_id,
        )
        session.add_all(
            [
                ReportingLineRecord(
                    book_id=stored.book_id,
                    transaction_id=payload.transaction_id,
                    classification_revision=payload.classification_revision,
                    line_id=line.line_id,
                    line_version_id=line.line_version_id,
                    catalog_id=line.catalog_id,
                    line_position=line.position,
                    asset_code=line.asset_code,
                    units=int(line.units),
                    line_kind=line.line_kind.value,
                    dimension=line.dimension.value,
                    dimension_id=line.dimension_id,
                    description_ref=line.description_ref,
                    source_event_id=stored.event_id,
                )
                for line in payload.lines
            ]
        )

    @staticmethod
    def _apply_reporting_lines_cleared(
        session: Session,
        stored: LedgerEventRecord,
        payload: ReportingLinesCleared,
    ) -> None:
        SynchronousProjector._validate_reporting_source(
            session,
            stored,
            transaction_id=payload.transaction_id,
            classification_revision=payload.classification_revision,
        )
        SynchronousProjector._delete_current_reporting_lines(
            session,
            book_id=stored.book_id,
            transaction_id=payload.transaction_id,
        )

    @staticmethod
    def _apply_external_reference_corrected(
        session: Session,
        stored: LedgerEventRecord,
        payload: FinancialExternalReferenceCorrected,
    ) -> None:
        if (
            stored.stream_type != "external_reference"
            or stored.stream_id != payload.transaction_id
        ):
            raise SynchronousProjectionError(
                "external-reference event identity does not match its transaction"
            )
        transaction = session.get(
            JournalTransactionRecord,
            (stored.book_id, payload.transaction_id),
        )
        if transaction is None:
            raise SynchronousProjectionError(
                "external-reference target is not projected in the same Book"
            )
        if transaction.source_position >= stored.book_position:
            raise SynchronousProjectionError(
                "external-reference target does not precede its correction"
            )
        key = (
            stored.book_id,
            payload.transaction_id,
            payload.provider_code,
            payload.reference_kind.value,
        )
        current = session.execute(
            select(TransactionExternalReferenceRecord)
            .where(
                TransactionExternalReferenceRecord.book_id == key[0],
                TransactionExternalReferenceRecord.transaction_id == key[1],
                TransactionExternalReferenceRecord.provider_code == key[2],
                TransactionExternalReferenceRecord.reference_kind == key[3],
            )
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if current is None:
            if payload.previous_reference is not None:
                raise SynchronousProjectionError(
                    "external-reference correction previous value is stale"
                )
            session.add(
                TransactionExternalReferenceRecord(
                    book_id=stored.book_id,
                    transaction_id=payload.transaction_id,
                    provider_code=payload.provider_code,
                    reference_kind=payload.reference_kind.value,
                    reference_value=payload.corrected_reference,
                    source_event_id=stored.event_id,
                )
            )
            return
        if current.reference_value != payload.previous_reference:
            raise SynchronousProjectionError(
                "external-reference correction previous value is stale"
            )
        current.reference_value = payload.corrected_reference
        current.source_event_id = stored.event_id

    @staticmethod
    def _validate_linked_investment_transaction(
        session: Session,
        stored: LedgerEventRecord,
        transaction_id: UUID,
    ) -> JournalTransactionRecord:
        transaction = session.get(
            JournalTransactionRecord,
            (stored.book_id, transaction_id),
        )
        if transaction is None:
            raise SynchronousProjectionError(
                "investment event has no linked transaction in the same Book"
            )
        if transaction.source_position >= stored.book_position:
            raise SynchronousProjectionError(
                "linked investment transaction must precede its lot event"
            )
        return transaction

    @staticmethod
    def _apply_investment_lot_acquired(
        session: Session,
        stored: LedgerEventRecord,
        payload: InvestmentLotAcquired,
    ) -> None:
        if (
            stored.stream_type != "investment_lot"
            or stored.stream_id != payload.lot_id
            or stored.stream_version != 1
        ):
            raise SynchronousProjectionError(
                "investment acquisition event identity is invalid"
            )
        SynchronousProjector._validate_linked_investment_transaction(
            session,
            stored,
            payload.transaction_id,
        )
        if session.get(InvestmentLotRecord, (stored.book_id, payload.lot_id)):
            raise SynchronousProjectionError("investment lot already exists")
        session.add(
            InvestmentLotRecord(
                book_id=stored.book_id,
                lot_id=payload.lot_id,
                acquisition_transaction_id=payload.transaction_id,
                instrument_asset_code=payload.instrument_asset_code,
                settlement_asset_code=payload.settlement_asset_code,
                acquired_quantity_units=int(payload.quantity_units),
                acquired_cost_units=int(payload.cost_units),
                fee_units=(
                    None if payload.fee_units is None else int(payload.fee_units)
                ),
                remaining_quantity_units=int(payload.quantity_units),
                remaining_cost_units=int(payload.cost_units),
                source_event_id=stored.event_id,
                source_position=stored.book_position,
            )
        )

    @staticmethod
    def _apply_investment_lot_disposed(
        session: Session,
        stored: LedgerEventRecord,
        payload: InvestmentLotDisposed,
    ) -> None:
        if (
            stored.stream_type != "investment_disposal"
            or stored.stream_id != payload.transaction_id
            or stored.stream_version != 1
        ):
            raise SynchronousProjectionError(
                "investment disposal event identity is invalid"
            )
        SynchronousProjector._validate_linked_investment_transaction(
            session,
            stored,
            payload.transaction_id,
        )
        for allocation in payload.allocations:
            lot = session.execute(
                select(InvestmentLotRecord)
                .where(
                    InvestmentLotRecord.book_id == stored.book_id,
                    InvestmentLotRecord.lot_id == allocation.lot_id,
                )
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if lot is None:
                raise SynchronousProjectionError(
                    "investment disposal references an unknown lot"
                )
            if (
                lot.instrument_asset_code != payload.instrument_asset_code
                or lot.settlement_asset_code != payload.settlement_asset_code
            ):
                raise SynchronousProjectionError(
                    "investment disposal lot belongs to a different asset pool"
                )
            quantity = int(allocation.quantity_units)
            remaining_quantity = int(lot.remaining_quantity_units)
            remaining_cost = int(lot.remaining_cost_units)
            if quantity > remaining_quantity:
                raise SynchronousProjectionError(
                    "investment disposal exceeds remaining lot quantity"
                )
            frozen_cost = int(allocation.cost_units)
            if frozen_cost > remaining_cost:
                raise SynchronousProjectionError(
                    "investment disposal exceeds remaining lot cost"
                )
            next_quantity = remaining_quantity - quantity
            next_cost = remaining_cost - frozen_cost
            if (next_quantity == 0) != (next_cost == 0):
                raise SynchronousProjectionError(
                    "investment disposal must deplete quantity and cost together"
                )
            lot.remaining_quantity_units = next_quantity
            lot.remaining_cost_units = next_cost
            lot.source_event_id = stored.event_id
            lot.source_position = stored.book_position
            session.add(
                InvestmentLotAllocationRecord(
                    book_id=stored.book_id,
                    allocation_id=allocation.allocation_id,
                    lot_id=allocation.lot_id,
                    disposal_transaction_id=payload.transaction_id,
                    allocation_position=allocation.position,
                    quantity_units=quantity,
                    cost_units=frozen_cost,
                    source_event_id=stored.event_id,
                    source_position=stored.book_position,
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
