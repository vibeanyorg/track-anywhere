from __future__ import annotations

from enum import Enum
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


class ReportingLineKind(str, Enum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    TAX = "tax"
    INVESTMENT = "investment"


class ReportingDimension(str, Enum):
    CATEGORY = "category"
    PROJECT = "project"
    COUNTERPARTY = "counterparty"
    TAX = "tax"


class ReportingLine(FrozenContract):
    line_id: UUID
    line_version_id: UUID
    catalog_id: UUID
    position: StrictInt = Field(ge=0)
    asset_code: AssetCode
    units: CanonicalUnits
    line_kind: ReportingLineKind
    dimension: ReportingDimension
    dimension_id: UUID | None = None
    counterparty_id: UUID | None = None
    description_ref: UUID | None = None


class ReportingLinesAssigned(EventContract):
    event_type: ClassVar[str] = "ReportingLinesAssigned"

    transaction_id: UUID
    classification_revision: StrictInt = Field(gt=0)
    lines: tuple[ReportingLine, ...]

    @model_validator(mode="after")
    def validate_replace_all_snapshot(self) -> Self:
        validate_ordered_records(
            self.lines,
            minimum=1,
            unique_fields=("line_id", "line_version_id"),
        )
        return self


class ReportingLinesCleared(EventContract):
    event_type: ClassVar[str] = "ReportingLinesCleared"

    transaction_id: UUID
    classification_revision: StrictInt = Field(gt=0)
