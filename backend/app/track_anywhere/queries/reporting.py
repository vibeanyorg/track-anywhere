from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.reporting.events import (
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from ..infrastructure.db.models.event_store import (
    BookEventHeadRecord,
    LedgerEventRecord,
)
from ..serialization.canonical_json import JSONValue
from ..serialization.event_registry import PRODUCTION_EVENT_REGISTRY


@dataclass(frozen=True, slots=True)
class ReportingLine:
    transaction_id: UUID
    classification_revision: int
    line_id: UUID
    line_version_id: UUID
    catalog_id: UUID
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
    stored_events = session.scalars(
        select(LedgerEventRecord)
        .where(
            LedgerEventRecord.book_id == book_id,
            LedgerEventRecord.book_position <= as_of_book_position,
            LedgerEventRecord.event_type.in_(
                ("ReportingLinesAssigned", "ReportingLinesCleared")
            ),
        )
        .order_by(LedgerEventRecord.book_position)
    )
    current_by_transaction: dict[UUID, tuple[ReportingLine, ...]] = {}
    for stored in stored_events:
        payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            stored.event_type,
            stored.event_schema_version,
            cast(dict[str, JSONValue], stored.payload),
        )
        if type(payload) is ReportingLinesAssigned:
            current_by_transaction[payload.transaction_id] = tuple(
                ReportingLine(
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
                )
                for line in payload.lines
            )
        elif type(payload) is ReportingLinesCleared:
            current_by_transaction.pop(payload.transaction_id, None)

    return tuple(
        line
        for transaction_id in sorted(
            current_by_transaction,
            key=lambda value: value.int,
        )
        for line in current_by_transaction[transaction_id]
    )


__all__ = ["ReportingLine", "list_current_reporting_lines"]
