from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

from .events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
    LotDisposalAllocation,
)


class LotAllocationError(ValueError):
    """Base class for fail-closed lot state and allocation errors."""


class DuplicateLot(LotAllocationError):
    pass


class WrongLotPool(LotAllocationError):
    pass


class OverDisposal(LotAllocationError):
    pass


class InvalidCostAllocation(LotAllocationError):
    pass


class LotNotFound(LotAllocationError):
    pass


class AllocationMethod(StrEnum):
    FIFO = "fifo"
    SPECIFIC_ID = "specific_id"


@dataclass(frozen=True, slots=True)
class InvestmentEvent:
    source_position: int
    effective_at: datetime
    payload: InvestmentLotAcquired | InvestmentLotDisposed

    def __post_init__(self) -> None:
        _require_positive_int(self.source_position, label="source_position")
        _require_aware_datetime(self.effective_at, label="effective_at")
        if type(self.payload) not in {InvestmentLotAcquired, InvestmentLotDisposed}:
            raise LotAllocationError("payload must be an investment lot event")


@dataclass(frozen=True, slots=True)
class LotPosition:
    lot_id: UUID
    instrument_asset_code: str
    settlement_asset_code: str
    effective_at: datetime
    source_position: int
    remaining_quantity_units: int
    remaining_cost_units: int

    def __post_init__(self) -> None:
        if type(self.lot_id) is not UUID:
            raise LotAllocationError("lot_id must be a UUID")
        _require_asset_code(
            self.instrument_asset_code,
            label="instrument_asset_code",
        )
        _require_asset_code(
            self.settlement_asset_code,
            label="settlement_asset_code",
        )
        _require_aware_datetime(self.effective_at, label="effective_at")
        _require_positive_int(self.source_position, label="source_position")
        _require_nonnegative_int(
            self.remaining_quantity_units,
            label="remaining_quantity_units",
        )
        _require_nonnegative_int(
            self.remaining_cost_units,
            label="remaining_cost_units",
        )
        if (self.remaining_quantity_units == 0) != (self.remaining_cost_units == 0):
            raise LotAllocationError(
                "remaining quantity and cost must deplete together"
            )


@dataclass(frozen=True, slots=True)
class LotState:
    lots: tuple[LotPosition, ...]

    def __post_init__(self) -> None:
        if type(self.lots) is not tuple or any(
            type(lot) is not LotPosition for lot in self.lots
        ):
            raise LotAllocationError("lots must be an immutable LotPosition tuple")
        lot_ids = tuple(lot.lot_id for lot in self.lots)
        if len(lot_ids) != len(set(lot_ids)):
            raise DuplicateLot("lot state contains duplicate lot IDs")


@dataclass(frozen=True, slots=True)
class SpecificLotRequest:
    lot_id: UUID
    quantity_units: int

    def __post_init__(self) -> None:
        if type(self.lot_id) is not UUID:
            raise LotAllocationError("specific lot_id must be a UUID")
        _require_positive_int(self.quantity_units, label="specific quantity_units")


def reduce_investment_events(events: Sequence[InvestmentEvent]) -> LotState:
    """Rebuild current Book-wide lot state from authoritative events."""

    if not isinstance(events, Sequence):
        raise LotAllocationError("events must be an ordered sequence")
    lots: dict[UUID, LotPosition] = {}
    previous_position = 0
    for event in events:
        if type(event) is not InvestmentEvent:
            raise LotAllocationError("each item must be an InvestmentEvent")
        if event.source_position <= previous_position:
            raise LotAllocationError(
                "investment events must have strictly increasing source positions"
            )
        previous_position = event.source_position
        payload = event.payload
        if type(payload) is InvestmentLotAcquired:
            if payload.lot_id in lots:
                raise DuplicateLot("investment lot was acquired more than once")
            lots[payload.lot_id] = LotPosition(
                lot_id=payload.lot_id,
                instrument_asset_code=payload.instrument_asset_code,
                settlement_asset_code=payload.settlement_asset_code,
                effective_at=event.effective_at,
                source_position=event.source_position,
                remaining_quantity_units=int(payload.quantity_units),
                remaining_cost_units=int(payload.cost_units),
            )
            continue

        if type(payload) is not InvestmentLotDisposed:
            raise AssertionError("unreachable investment payload type")
        for allocation in payload.allocations:
            lot = lots.get(allocation.lot_id)
            if lot is None:
                raise LotNotFound("disposal allocation references an unknown lot")
            _require_pool(
                lot,
                instrument_asset_code=payload.instrument_asset_code,
                settlement_asset_code=payload.settlement_asset_code,
            )
            quantity = int(allocation.quantity_units)
            cost = int(allocation.cost_units)
            if quantity > lot.remaining_quantity_units:
                raise OverDisposal("stored allocation exceeds remaining lot quantity")
            if cost > lot.remaining_cost_units:
                raise InvalidCostAllocation(
                    "stored allocation exceeds remaining lot cost"
                )
            remaining_quantity = lot.remaining_quantity_units - quantity
            remaining_cost = lot.remaining_cost_units - cost
            if (remaining_quantity == 0) != (remaining_cost == 0):
                raise InvalidCostAllocation(
                    "stored allocation must deplete quantity and cost together"
                )
            lots[lot.lot_id] = replace(
                lot,
                remaining_quantity_units=remaining_quantity,
                remaining_cost_units=remaining_cost,
            )

    return LotState(lots=tuple(sorted(lots.values(), key=_fifo_key)))


def select_lot_allocations(
    state: LotState,
    *,
    instrument_asset_code: str,
    settlement_asset_code: str,
    quantity_units: int,
    method: AllocationMethod,
    command_id: UUID,
    specific_lots: tuple[SpecificLotRequest, ...] = (),
) -> tuple[LotDisposalAllocation, ...]:
    """Select and freeze deterministic FIFO or Specific-ID allocations."""

    if type(state) is not LotState:
        raise LotAllocationError("state must be a LotState")
    _require_asset_code(instrument_asset_code, label="instrument_asset_code")
    _require_asset_code(settlement_asset_code, label="settlement_asset_code")
    _require_positive_int(quantity_units, label="quantity_units")
    if type(method) is not AllocationMethod:
        raise LotAllocationError("method must be an AllocationMethod")
    if type(command_id) is not UUID:
        raise LotAllocationError("command_id must be a UUID")
    if type(specific_lots) is not tuple or any(
        type(request) is not SpecificLotRequest for request in specific_lots
    ):
        raise LotAllocationError(
            "specific_lots must be an immutable SpecificLotRequest tuple"
        )

    if method is AllocationMethod.FIFO:
        if specific_lots:
            raise LotAllocationError("FIFO does not accept specific lot requests")
        selected = _select_fifo(
            state,
            instrument_asset_code=instrument_asset_code,
            settlement_asset_code=settlement_asset_code,
            quantity_units=quantity_units,
        )
    else:
        selected = _select_specific(
            state,
            instrument_asset_code=instrument_asset_code,
            settlement_asset_code=settlement_asset_code,
            quantity_units=quantity_units,
            requests=specific_lots,
        )

    allocations: list[LotDisposalAllocation] = []
    for position, (lot, quantity) in enumerate(selected):
        cost = _allocated_cost(lot, quantity)
        allocations.append(
            LotDisposalAllocation(
                allocation_id=uuid5(
                    command_id,
                    f"{position}:{lot.lot_id}:{quantity}:{cost}",
                ),
                lot_id=lot.lot_id,
                position=position,
                quantity_units=str(quantity),
                cost_units=str(cost),
            )
        )
    return tuple(allocations)


def _select_fifo(
    state: LotState,
    *,
    instrument_asset_code: str,
    settlement_asset_code: str,
    quantity_units: int,
) -> tuple[tuple[LotPosition, int], ...]:
    candidates = tuple(
        sorted(
            (
                lot
                for lot in state.lots
                if lot.instrument_asset_code == instrument_asset_code
                and lot.settlement_asset_code == settlement_asset_code
                and lot.remaining_quantity_units > 0
            ),
            key=_fifo_key,
        )
    )
    if sum(lot.remaining_quantity_units for lot in candidates) < quantity_units:
        raise OverDisposal("disposal exceeds available lot quantity")

    remaining = quantity_units
    selected: list[tuple[LotPosition, int]] = []
    for lot in candidates:
        if remaining == 0:
            break
        quantity = min(remaining, lot.remaining_quantity_units)
        selected.append((lot, quantity))
        remaining -= quantity
    return tuple(selected)


def _select_specific(
    state: LotState,
    *,
    instrument_asset_code: str,
    settlement_asset_code: str,
    quantity_units: int,
    requests: tuple[SpecificLotRequest, ...],
) -> tuple[tuple[LotPosition, int], ...]:
    if not requests:
        raise LotAllocationError("Specific ID requires at least one lot request")
    requested_ids = tuple(request.lot_id for request in requests)
    if len(requested_ids) != len(set(requested_ids)):
        raise DuplicateLot("Specific ID contains a duplicate lot")
    if sum(request.quantity_units for request in requests) != quantity_units:
        raise LotAllocationError(
            "Specific ID quantities must equal the disposal quantity"
        )

    by_id = {lot.lot_id: lot for lot in state.lots}
    selected: list[tuple[LotPosition, int]] = []
    for request in requests:
        lot = by_id.get(request.lot_id)
        if lot is None:
            raise LotNotFound("Specific ID references an unknown lot")
        _require_pool(
            lot,
            instrument_asset_code=instrument_asset_code,
            settlement_asset_code=settlement_asset_code,
        )
        if request.quantity_units > lot.remaining_quantity_units:
            raise OverDisposal("Specific ID exceeds remaining lot quantity")
        selected.append((lot, request.quantity_units))
    return tuple(selected)


def _allocated_cost(lot: LotPosition, quantity_units: int) -> int:
    _require_positive_int(quantity_units, label="allocation quantity_units")
    if quantity_units > lot.remaining_quantity_units:
        raise OverDisposal("allocation exceeds remaining lot quantity")
    if quantity_units == lot.remaining_quantity_units:
        return lot.remaining_cost_units
    cost = lot.remaining_cost_units * quantity_units // lot.remaining_quantity_units
    if cost == 0:
        raise InvalidCostAllocation("partial allocation rounds to zero cost units")
    return cost


def _require_pool(
    lot: LotPosition,
    *,
    instrument_asset_code: str,
    settlement_asset_code: str,
) -> None:
    if (
        lot.instrument_asset_code != instrument_asset_code
        or lot.settlement_asset_code != settlement_asset_code
    ):
        raise WrongLotPool("lot does not belong to the requested asset pool")


def _fifo_key(lot: LotPosition) -> tuple[datetime, int, UUID]:
    return lot.effective_at, lot.source_position, lot.lot_id


def _require_asset_code(value: object, *, label: str) -> str:
    if type(value) is not str or not value or len(value) > 16 or value.upper() != value:
        raise LotAllocationError(f"{label} must be a canonical asset code")
    return value


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise LotAllocationError(f"{label} must be a positive integer")
    return value


def _require_nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise LotAllocationError(f"{label} must be a nonnegative integer")
    return value


def _require_aware_datetime(value: object, *, label: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise LotAllocationError(f"{label} must be timezone-aware")
    return value


__all__ = [
    "AllocationMethod",
    "DuplicateLot",
    "InvalidCostAllocation",
    "InvestmentEvent",
    "LotAllocationError",
    "LotNotFound",
    "LotPosition",
    "LotState",
    "OverDisposal",
    "SpecificLotRequest",
    "WrongLotPool",
    "reduce_investment_events",
    "select_lot_allocations",
]
