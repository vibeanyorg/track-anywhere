from __future__ import annotations

from enum import Enum
from typing import Annotated, ClassVar, Self
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, model_validator

from ..privacy import (
    AssetCode,
    CanonicalEventHash,
    CanonicalUnits,
    EventContract,
    FrozenContract,
)


SourceIdentity = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
]


class ExactImportedDecimal(FrozenContract):
    unscaled_units: CanonicalUnits
    scale: StrictInt = Field(ge=0, le=30)


class HistoricalInvestmentActivityKind(str, Enum):
    BUY = "buy"
    SELL = "sell"


class HistoricalInvestmentActivityImported(EventContract):
    event_type: ClassVar[str] = "HistoricalInvestmentActivityImported"

    source_event_id: SourceIdentity
    source_account_id: SourceIdentity
    activity_kind: HistoricalInvestmentActivityKind
    settlement_asset_code: AssetCode
    cash_amount: ExactImportedDecimal
    quantity: ExactImportedDecimal | None = None
    nav: ExactImportedDecimal | None = None
    source_version: StrictInt = Field(ge=1)
    source_row_hash: CanonicalEventHash


class HistoricalReportingLineKind(str, Enum):
    FX_EXCHANGE = "fx_exchange"
    FX_FEE = "fx_fee"


class HistoricalReportingLineImported(EventContract):
    event_type: ClassVar[str] = "HistoricalReportingLineImported"

    source_line_id: SourceIdentity
    source_transaction_id: SourceIdentity
    transaction_id: UUID
    line_kind: HistoricalReportingLineKind
    position: StrictInt = Field(ge=0)
    asset_code: AssetCode
    amount: ExactImportedDecimal
    source_version: StrictInt = Field(ge=1)
    source_row_hash: CanonicalEventHash


class HistoricalCategoryActivityKind(str, Enum):
    CREATE = "create"
    RECLASSIFY = "reclassify"


class HistoricalCategoryActivityImported(EventContract):
    event_type: ClassVar[str] = "HistoricalCategoryActivityImported"

    source_event_id: SourceIdentity
    activity_kind: HistoricalCategoryActivityKind
    source_category_id: SourceIdentity
    target_category_id: SourceIdentity | None = None
    affected_line_count: StrictInt = Field(ge=0)
    source_actor_hash: CanonicalEventHash
    source_version: StrictInt = Field(ge=1)
    before_hash: CanonicalEventHash
    after_hash: CanonicalEventHash
    rollback_hash: CanonicalEventHash
    source_row_hash: CanonicalEventHash

    @model_validator(mode="after")
    def validate_category_direction(self) -> Self:
        if self.activity_kind is HistoricalCategoryActivityKind.CREATE:
            if self.target_category_id is not None:
                raise ValueError("historical category create target must be null")
        elif self.target_category_id is None:
            raise ValueError("historical reclassification target is required")
        return self


__all__ = [
    "ExactImportedDecimal",
    "HistoricalCategoryActivityImported",
    "HistoricalCategoryActivityKind",
    "HistoricalInvestmentActivityImported",
    "HistoricalInvestmentActivityKind",
    "HistoricalReportingLineImported",
    "HistoricalReportingLineKind",
]
