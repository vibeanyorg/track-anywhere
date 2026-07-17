from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...domain.credit_cards.events import (
    CreditCardIntent,
    CreditCardTransactionRecorded,
)
from ...domain.journal.events import (
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ...domain.reporting.events import (
    ReportingDimension,
    ReportingLine,
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.async_projection import ProjectionCheckpointRecord
from ..db.models.event_store import LedgerEventRecord
from ..db.models.monthly_summary import MonthlyCategorySummaryRecord
from .checkpoints import PROJECTION_NAME, PROJECTOR_VERSION
from .dirty_periods import month_start, utc_date


@dataclass(frozen=True, slots=True, order=True)
class MonthlySummaryValue:
    category_id: UUID
    category_version_id: UUID
    asset_code: str
    line_kind: str
    units: int


def cold_replay_monthly_summary(
    session: Session,
    book_id: UUID,
    *,
    through_book_position: int | None = None,
) -> dict[date, tuple[MonthlySummaryValue, ...]]:
    statement = select(LedgerEventRecord).where(LedgerEventRecord.book_id == book_id)
    if through_book_position is not None:
        statement = statement.where(
            LedgerEventRecord.book_position <= through_book_position
        )
    records = tuple(
        session.scalars(statement.order_by(LedgerEventRecord.book_position))
    )
    transaction_times: dict[UUID, date] = {}
    transaction_reporting_signs: dict[UUID, int] = {}
    transaction_card_intents: dict[UUID, CreditCardIntent] = {}
    reporting: dict[UUID, tuple[ReportingLine, ...]] = {}
    reversals: dict[UUID, date] = {}
    reversal_transaction_ids: set[UUID] = set()
    for record in records:
        payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            record.event_type,
            record.event_schema_version,
            record.payload,
        )
        if type(payload) is JournalTransactionPosted:
            transaction_times[payload.transaction_id] = utc_date(record.effective_at)
            transaction_reporting_signs[payload.transaction_id] = 1
        elif type(payload) is CreditCardTransactionRecorded:
            transaction_times[payload.transaction_id] = utc_date(record.effective_at)
            transaction_card_intents[payload.transaction_id] = payload.intent
            transaction_reporting_signs[payload.transaction_id] = {
                CreditCardIntent.CHARGE: 1,
                CreditCardIntent.FEE: 1,
                CreditCardIntent.PAYMENT: 0,
                CreditCardIntent.REFUND: -1,
            }[payload.intent]
        elif type(payload) is JournalTransactionReversed:
            effective_date = utc_date(record.effective_at)
            transaction_times[payload.reversal_transaction_id] = effective_date
            reversals[payload.reverses_transaction_id] = effective_date
            reversal_transaction_ids.add(payload.reversal_transaction_id)
        elif type(payload) is ReportingLinesAssigned:
            if payload.transaction_id in reversal_transaction_ids:
                raise ValueError(
                    "reversal transactions cannot define independent reporting lines"
                )
            card_intent = transaction_card_intents.get(payload.transaction_id)
            if card_intent is CreditCardIntent.PAYMENT:
                raise ValueError("credit-card payments cannot have reporting lines")
            if card_intent in {
                CreditCardIntent.CHARGE,
                CreditCardIntent.FEE,
                CreditCardIntent.REFUND,
            } and any(
                line.line_kind.value != "expense" for line in payload.lines
            ):
                raise ValueError(
                    "credit-card reporting lines must use expense semantics"
                )
            reporting[payload.transaction_id] = payload.lines
        elif type(payload) is ReportingLinesCleared:
            reporting.pop(payload.transaction_id, None)

    totals: dict[tuple[date, UUID, UUID, str, str], int] = {}
    for transaction_id, lines in reporting.items():
        effective_date = transaction_times.get(transaction_id)
        if effective_date is None:
            continue
        reporting_sign = transaction_reporting_signs.get(transaction_id, 1)
        if reporting_sign == 0:
            continue
        _add_lines(
            totals,
            month_start(effective_date),
            lines,
            sign=reporting_sign,
        )
        reversed_at = reversals.get(transaction_id)
        if reversed_at is not None:
            _add_lines(
                totals,
                month_start(reversed_at),
                lines,
                sign=-reporting_sign,
            )
    by_period: dict[date, list[MonthlySummaryValue]] = {}
    for (
        period,
        category_id,
        version_id,
        asset_code,
        line_kind,
    ), units in totals.items():
        if units == 0:
            continue
        by_period.setdefault(period, []).append(
            MonthlySummaryValue(
                category_id=category_id,
                category_version_id=version_id,
                asset_code=asset_code,
                line_kind=line_kind,
                units=units,
            )
        )
    return {
        period: tuple(sorted(values)) for period, values in sorted(by_period.items())
    }


def _add_lines(
    totals: dict[tuple[date, UUID, UUID, str, str], int],
    period: date,
    lines: tuple[ReportingLine, ...],
    *,
    sign: int,
) -> None:
    for line in lines:
        if (
            line.dimension is not ReportingDimension.CATEGORY
            or line.dimension_id is None
        ):
            continue
        key = (
            period,
            line.dimension_id,
            line.catalog_id,
            line.asset_code,
            line.line_kind.value,
        )
        totals[key] = totals.get(key, 0) + sign * int(line.units)


def replace_periods(
    session: Session,
    *,
    book_id: UUID,
    generation: int,
    periods: tuple[date, ...],
    through_book_position: int,
) -> None:
    replay = cold_replay_monthly_summary(
        session,
        book_id,
        through_book_position=through_book_position,
    )
    for period in periods:
        session.execute(
            delete(MonthlyCategorySummaryRecord).where(
                MonthlyCategorySummaryRecord.projection_name == PROJECTION_NAME,
                MonthlyCategorySummaryRecord.projector_version == PROJECTOR_VERSION,
                MonthlyCategorySummaryRecord.book_id == book_id,
                MonthlyCategorySummaryRecord.generation == generation,
                MonthlyCategorySummaryRecord.period_start == period,
            )
        )
        session.add_all(
            MonthlyCategorySummaryRecord(
                projection_name=PROJECTION_NAME,
                projector_version=PROJECTOR_VERSION,
                book_id=book_id,
                generation=generation,
                period_start=period,
                category_id=value.category_id,
                category_version_id=value.category_version_id,
                asset_code=value.asset_code,
                line_kind=value.line_kind,
                units=value.units,
                as_of_book_position=through_book_position,
            )
            for value in replay.get(period, ())
        )
    session.flush()


def replace_generation(
    session: Session,
    *,
    book_id: UUID,
    generation: int,
    through_book_position: int,
) -> None:
    replay = cold_replay_monthly_summary(
        session,
        book_id,
        through_book_position=through_book_position,
    )
    session.execute(
        delete(MonthlyCategorySummaryRecord).where(
            MonthlyCategorySummaryRecord.projection_name == PROJECTION_NAME,
            MonthlyCategorySummaryRecord.projector_version == PROJECTOR_VERSION,
            MonthlyCategorySummaryRecord.book_id == book_id,
            MonthlyCategorySummaryRecord.generation == generation,
        )
    )
    session.add_all(
        MonthlyCategorySummaryRecord(
            projection_name=PROJECTION_NAME,
            projector_version=PROJECTOR_VERSION,
            book_id=book_id,
            generation=generation,
            period_start=period,
            category_id=value.category_id,
            category_version_id=value.category_version_id,
            asset_code=value.asset_code,
            line_kind=value.line_kind,
            units=value.units,
            as_of_book_position=through_book_position,
        )
        for period, values in replay.items()
        for value in values
    )
    session.flush()


def read_monthly_summary(
    session: Session,
    book_id: UUID,
    *,
    period_start: date,
) -> tuple[MonthlySummaryValue, ...]:
    checkpoint = session.get(
        ProjectionCheckpointRecord,
        (PROJECTION_NAME, PROJECTOR_VERSION, book_id),
    )
    if checkpoint is None:
        return ()
    rows = session.scalars(
        select(MonthlyCategorySummaryRecord)
        .where(
            MonthlyCategorySummaryRecord.projection_name == PROJECTION_NAME,
            MonthlyCategorySummaryRecord.projector_version == PROJECTOR_VERSION,
            MonthlyCategorySummaryRecord.book_id == book_id,
            MonthlyCategorySummaryRecord.generation == checkpoint.active_generation,
            MonthlyCategorySummaryRecord.period_start == period_start,
        )
        .order_by(
            MonthlyCategorySummaryRecord.category_id,
            MonthlyCategorySummaryRecord.category_version_id,
            MonthlyCategorySummaryRecord.asset_code,
            MonthlyCategorySummaryRecord.line_kind,
        )
    )
    return tuple(
        MonthlySummaryValue(
            category_id=row.category_id,
            category_version_id=row.category_version_id,
            asset_code=row.asset_code,
            line_kind=row.line_kind,
            units=int(row.units),
        )
        for row in rows
    )


__all__ = [
    "MonthlySummaryValue",
    "cold_replay_monthly_summary",
    "read_monthly_summary",
    "replace_generation",
    "replace_periods",
]
