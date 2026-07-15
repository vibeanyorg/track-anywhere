from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from ...db.models.event_store import LedgerEventRecord
from ...db.models.investments import (
    InvestmentLotAllocationRecord,
    InvestmentLotRecord,
)
from ...db.models.projections import JournalTransactionRecord
from .contracts import SynchronousProjectionError


def _validate_linked_transaction(
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


def apply_investment_lot_acquired(
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
    _validate_linked_transaction(session, stored, payload.transaction_id)
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
            fee_units=None if payload.fee_units is None else int(payload.fee_units),
            remaining_quantity_units=int(payload.quantity_units),
            remaining_cost_units=int(payload.cost_units),
            source_event_id=stored.event_id,
            source_position=stored.book_position,
        )
    )


def apply_investment_lot_disposed(
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
    _validate_linked_transaction(session, stored, payload.transaction_id)
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


__all__ = ["apply_investment_lot_acquired", "apply_investment_lot_disposed"]
