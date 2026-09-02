from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ....domain.reporting.events import (
    ReportingDimension,
    ReportingLineKind,
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from ...db.models.catalog import CategoryVersionRecord
from ...db.models.event_store import LedgerEventRecord
from ...db.models.projections import (
    JournalPostingRecord,
    JournalTransactionRecord,
    ReportingLineRecord,
    TransactionReversalRecord,
)
from .contracts import SynchronousProjectionError


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
    if (
        session.get(
            TransactionReversalRecord,
            (stored.book_id, transaction_id),
        )
        is not None
    ):
        raise SynchronousProjectionError(
            "reversal transactions inherit the original reporting lines"
        )
    return transaction


def _validate_reporting_lines(
    session: Session,
    stored: LedgerEventRecord,
    payload: ReportingLinesAssigned,
    transaction: JournalTransactionRecord,
) -> None:
    source_event = session.get(LedgerEventRecord, transaction.source_event_id)
    if (
        transaction.transaction_kind == "credit_card_payment"
        and (
            source_event is None
            or source_event.event_type != "JournalTransactionPosted"
        )
    ):
        raise SynchronousProjectionError(
            "credit-card payments cannot have reporting lines"
        )
    if transaction.transaction_kind in {
        "credit_card_charge",
        "credit_card_fee",
        "credit_card_refund",
    } and any(
        line.line_kind is not ReportingLineKind.EXPENSE for line in payload.lines
    ):
        raise SynchronousProjectionError(
            "credit-card reporting lines must use expense semantics"
        )
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


def apply_reporting_lines_assigned(
    session: Session,
    stored: LedgerEventRecord,
    payload: ReportingLinesAssigned,
) -> None:
    transaction = _validate_reporting_source(
        session,
        stored,
        transaction_id=payload.transaction_id,
        classification_revision=payload.classification_revision,
    )
    _validate_reporting_lines(session, stored, payload, transaction)
    _delete_current_reporting_lines(
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


def apply_reporting_lines_cleared(
    session: Session,
    stored: LedgerEventRecord,
    payload: ReportingLinesCleared,
) -> None:
    _validate_reporting_source(
        session,
        stored,
        transaction_id=payload.transaction_id,
        classification_revision=payload.classification_revision,
    )
    _delete_current_reporting_lines(
        session,
        book_id=stored.book_id,
        transaction_id=payload.transaction_id,
    )


__all__ = ["apply_reporting_lines_assigned", "apply_reporting_lines_cleared"]
