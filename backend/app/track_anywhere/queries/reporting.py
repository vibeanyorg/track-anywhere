from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.event_store import BookEventHeadRecord
from ..infrastructure.db.models.projections import (
    JournalTransactionRecord,
    ReportingLineRecord,
)


@dataclass(frozen=True, slots=True)
class ReportingLine:
    transaction_id: UUID
    classification_revision: int
    line_id: UUID
    line_version_id: UUID
    line_position: int
    asset_code: str
    units: int
    line_kind: str
    dimension: str
    dimension_id: UUID | None


def list_current_reporting_lines(
    session: Session,
    book_id: UUID,
    *,
    as_of_book_position: int,
) -> tuple[ReportingLine, ...]:
    if type(book_id) is not UUID:
        raise ValueError("book_id must be a UUID")
    head = session.scalar(
        select(BookEventHeadRecord.last_position).where(
            BookEventHeadRecord.book_id == book_id
        )
    )
    if head is None:
        raise LookupError("Book not found")
    if type(as_of_book_position) is not int or not 0 <= as_of_book_position <= head:
        raise ValueError("as_of_book_position is outside the Book head")
    latest = (
        select(
            ReportingLineRecord.transaction_id.label("transaction_id"),
            func.max(ReportingLineRecord.classification_revision).label("revision"),
        )
        .join(
            JournalTransactionRecord,
            (JournalTransactionRecord.book_id == ReportingLineRecord.book_id)
            & (
                JournalTransactionRecord.transaction_id
                == ReportingLineRecord.transaction_id
            ),
        )
        .where(
            ReportingLineRecord.book_id == book_id,
            JournalTransactionRecord.source_position <= as_of_book_position,
        )
        .group_by(ReportingLineRecord.transaction_id)
        .subquery()
    )
    rows = session.scalars(
        select(ReportingLineRecord)
        .join(
            latest,
            (latest.c.transaction_id == ReportingLineRecord.transaction_id)
            & (latest.c.revision == ReportingLineRecord.classification_revision),
        )
        .where(ReportingLineRecord.book_id == book_id)
        .order_by(
            ReportingLineRecord.transaction_id,
            ReportingLineRecord.line_position,
        )
    )
    return tuple(
        ReportingLine(
            transaction_id=row.transaction_id,
            classification_revision=row.classification_revision,
            line_id=row.line_id,
            line_version_id=row.line_version_id,
            line_position=row.line_position,
            asset_code=row.asset_code,
            units=int(row.units),
            line_kind=row.line_kind,
            dimension=row.dimension,
            dimension_id=row.dimension_id,
        )
        for row in rows
    )


__all__ = ["ReportingLine", "list_current_reporting_lines"]
