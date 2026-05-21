from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .commands import ASSET_CODE_PATTERN, CATEGORY_KINDS, StrictCommand


class CreateBookCommand(StrictCommand):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["personal", "family", "travel", "business", "reimbursement", "custom"] = "personal"
    base_currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    template_key: str | None = Field(default=None, min_length=1, max_length=80)


class UpdateCategoryCommand(StrictCommand):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: str | None = None
    icon: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, min_length=1, max_length=32)
    sort_order: int | None = None
    status: Literal["active", "hidden", "archived"] | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.strip().split())
        if not stripped:
            raise ValueError("category name must not be blank")
        return stripped


class AddCategoryAliasCommand(StrictCommand):
    alias: str = Field(min_length=1, max_length=80)
    source: Literal["manual", "import", "ai", "migration"] = "manual"


class MergeCategoryCommand(StrictCommand):
    target_category_id: str


class ReverseBookTransactionCommand(StrictCommand):
    memo: str = Field(min_length=1, max_length=256)


class CreateBudgetCommand(StrictCommand):
    name: str = Field(min_length=1, max_length=120)
    period: Literal["monthly", "weekly", "yearly", "custom"]
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    total_amount: Decimal = Field(gt=0)
    starts_on: date | None = None
    ends_on: date | None = None
    rollover_policy: Literal["none", "carry_remaining", "carry_overspend"] = "none"


class CreateBudgetTargetCommand(StrictCommand):
    target_type: Literal["book", "category_node", "category_subtree", "project", "merchant"]
    target_id: str | None = None
    mode: Literal["include", "exclude"] = "include"
    amount: Decimal | None = Field(default=None, gt=0)
    metadata: dict[str, str] = Field(default_factory=dict)

class RecordFxExchangeCommand(StrictCommand):
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    from_account_id: str
    from_amount: Decimal = Field(gt=0)
    from_currency: str = Field(pattern=ASSET_CODE_PATTERN)
    to_account_id: str
    to_amount: Decimal = Field(gt=0)
    to_currency: str = Field(pattern=ASSET_CODE_PATTERN)
    purpose: str = Field(default="fx_exchange", min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    rate_source: str = Field(default="manual", min_length=1, max_length=80)
    fee_account_id: str | None = None
    fee_amount: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_exchange_shape(self):
        if self.from_currency == self.to_currency:
            raise ValueError("FX exchange requires different currencies")
        if (self.fee_account_id is None) != (self.fee_amount is None):
            raise ValueError("fee_account_id and fee_amount must be provided together")
        return self


class RecordInvestmentValuationCommand(StrictCommand):
    account_id: str
    value: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(default="manual", min_length=1, max_length=80)
    memo: str = Field(default="", max_length=256)
