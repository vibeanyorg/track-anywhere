from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ASSET_CODE_PATTERN = r"^[A-Z][A-Z0-9]{1,15}$"
INSTITUTION_TYPES = Literal["bank", "e_wallet", "fintech", "brokerage", "cash", "crypto_wallet", "system", "other"]
CATEGORY_KINDS = Literal["income", "expense"]


class StrictCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["v1"] = "v1"


class MonthlyRecurrenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["monthly_day"]
    day: int = Field(ge=1, le=31)


class YearlyRecurrenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["yearly_date"]
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)


class CreateAccountCommand(StrictCommand):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["asset", "liability", "income", "expense", "equity", "fund", "system"]
    currency: str = Field(pattern=ASSET_CODE_PATTERN)
    opening_balance: Decimal = Decimal("0")
    book_id: str | None = None
    institution_type: INSTITUTION_TYPES | None = None
    subtype: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    institution: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("institution")
    @classmethod
    def normalize_empty_institution(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("institution must not be blank")
        return stripped


class UpdateAccountMetadataCommand(StrictCommand):
    institution_type: INSTITUTION_TYPES | None = None
    subtype: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    institution: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("institution")
    @classmethod
    def normalize_empty_institution(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("institution must not be blank")
        return stripped


class CreateUserCommand(StrictCommand):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class CaptureDraftCommand(StrictCommand):
    memo: str = Field(min_length=1, max_length=256)
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    source_account_id: str | None = None
    expense_account_id: str | None = None
    fund_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("memo")
    @classmethod
    def reject_policy_override_text(cls, value: str) -> str:
        lowered = value.lower()
        forbidden = ["ignore policy", "requires_confirmation=false", "ledger_impact", "actor=", "scope="]
        if any(item in lowered for item in forbidden):
            raise ValueError("memo contains policy override text")
        return value


class RecordTransactionCommand(StrictCommand):
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    from_account_id: str
    to_account_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    category_id: str | None = None


class CreateCategoryCommand(StrictCommand):
    kind: CATEGORY_KINDS
    name: str = Field(min_length=1, max_length=80)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_category_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.strip().split())
        if not stripped:
            raise ValueError("category label must not be blank")
        return stripped


class CreateRecurringItemCommand(StrictCommand):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["paid", "reminder_only"]
    book_id: str | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=ASSET_CODE_PATTERN)
    provider: str | None = Field(default=None, min_length=1, max_length=120)
    reference: str | None = Field(default=None, min_length=1, max_length=120)
    recurrence: MonthlyRecurrenceCommand | YearlyRecurrenceCommand
    reminder_days: list[int] = Field(min_length=1, max_length=30)
    anchor_date: date
    source_account_id: str | None = None
    category_id: str | None = None

    @field_validator("reminder_days")
    @classmethod
    def normalize_reminder_days(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("reminder days must be unique")
        if any(day < 1 or day > 365 for day in value):
            raise ValueError("reminder days must be between 1 and 365")
        return sorted(value, reverse=True)


class UpdateRecurringItemCommand(StrictCommand):
    status: Literal["active", "paused", "cancelled"] | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=ASSET_CODE_PATTERN)
    provider: str | None = Field(default=None, min_length=1, max_length=120)
    reference: str | None = Field(default=None, min_length=1, max_length=120)
    recurrence: MonthlyRecurrenceCommand | YearlyRecurrenceCommand | None = None
    reminder_days: list[int] | None = Field(default=None, min_length=1, max_length=30)
    anchor_date: date | None = None
    source_account_id: str | None = None
    category_id: str | None = None

    @field_validator("reminder_days")
    @classmethod
    def normalize_optional_reminder_days(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("reminder days must be unique")
        if any(day < 1 or day > 365 for day in value):
            raise ValueError("reminder days must be between 1 and 365")
        return sorted(value, reverse=True)


class CheckRecurringCommand(StrictCommand):
    as_of: date = Field(default_factory=date.today)
    window_days: int = Field(default=0, ge=0, le=365)


class GenerateRecurringDraftsCommand(StrictCommand):
    as_of: date = Field(default_factory=date.today)


class RecordExpenseCommand(StrictCommand):
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    from_account_id: str
    category_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)


class RecordIncomeCommand(StrictCommand):
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    to_account_id: str
    category_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)


class UpdateCreditCardProfileCommand(StrictCommand):
    credit_limit: Decimal | None = Field(default=None, ge=0)
    available_credit: Decimal | None = Field(default=None, ge=0)
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    annual_fee: Decimal | None = Field(default=None, ge=0)


class BalanceAdjustmentCommand(StrictCommand):
    account_id: str
    amount: Decimal
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)

    @field_validator("amount")
    @classmethod
    def reject_zero_amount(cls, value: Decimal) -> Decimal:
        if value == Decimal("0"):
            raise ValueError("amount must not be zero")
        return value


class RecordInvestmentEventCommand(StrictCommand):
    account_id: str
    event_type: Literal["buy", "add", "sell", "income"]
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    memo: str = Field(default="", max_length=256)
    units: Decimal | None = Field(default=None, gt=0)
    nav: Decimal | None = Field(default=None, gt=0)
    transaction_id: str | None = None
    cash_account_id: str | None = None


class ConfirmDraftCommand(StrictCommand):
    draft_id: str
    expected_version: int = Field(ge=1)


class RejectDraftCommand(StrictCommand):
    draft_id: str
    expected_version: int = Field(ge=1)
    reason: str = Field(default="", max_length=240)


class SupersedeDraftCommand(StrictCommand):
    draft_id: str
    expected_version: int = Field(ge=1)
    replacement: CaptureDraftCommand


class CreateFundCommand(StrictCommand):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)


class FundAllocationCommand(StrictCommand):
    fund_id: str
    source_account_id: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    expected_version: int = Field(ge=1)
    memo: str = Field(default="", max_length=256)


class FundSpendCommand(StrictCommand):
    fund_id: str
    expense_account_id: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    expected_version: int = Field(ge=1)
    memo: str = Field(default="", max_length=256)


class ReverseTransactionCommand(StrictCommand):
    transaction_id: str
    memo: str = Field(min_length=1, max_length=256)


from .credential_commands import IssueCredentialCommand, RevokeCredentialByIdCommand, RevokeCredentialCommand


class ReconciliationActionCommand(StrictCommand):
    summary: str = Field(min_length=1, max_length=500)
