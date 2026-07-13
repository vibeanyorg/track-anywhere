from __future__ import annotations

from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from ..privacy import (
    AssetCode,
    CanonicalUnits,
    EventContract,
    FrozenContract,
    validate_ordered_records,
)


class LotDisposalAllocation(FrozenContract):
    allocation_id: UUID
    lot_id: UUID
    position: StrictInt = Field(ge=0)
    quantity_units: CanonicalUnits
    cost_units: CanonicalUnits


class InvestmentLotAcquired(EventContract):
    event_type: ClassVar[str] = "InvestmentLotAcquired"

    transaction_id: UUID
    lot_id: UUID
    instrument_asset_code: AssetCode
    settlement_asset_code: AssetCode
    quantity_units: CanonicalUnits
    cost_units: CanonicalUnits
    fee_units: CanonicalUnits | None = None


class InvestmentLotDisposed(EventContract):
    event_type: ClassVar[str] = "InvestmentLotDisposed"

    transaction_id: UUID
    instrument_asset_code: AssetCode
    settlement_asset_code: AssetCode
    quantity_units: CanonicalUnits
    proceeds_units: CanonicalUnits
    cost_basis_units: CanonicalUnits
    fee_units: CanonicalUnits | None = None
    allocations: tuple[LotDisposalAllocation, ...]

    @model_validator(mode="after")
    def validate_final_allocations(self) -> Self:
        validate_ordered_records(
            self.allocations,
            minimum=1,
            unique_fields=("allocation_id", "lot_id"),
        )
        allocated_quantity = sum(
            int(allocation.quantity_units) for allocation in self.allocations
        )
        if allocated_quantity != int(self.quantity_units):
            raise ValueError("lot allocation quantities must equal disposal quantity")
        allocated_cost = sum(
            int(allocation.cost_units) for allocation in self.allocations
        )
        if allocated_cost != int(self.cost_basis_units):
            raise ValueError("lot allocation costs must equal disposal cost basis")
        return self
