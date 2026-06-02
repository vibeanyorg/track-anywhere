from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .posting_semantics import (
    posting_semantics_review_decision_schema_extra,
    public_write_posting_semantics_schema_extra,
)


ASSET_CODE_PATTERN = r"^[A-Z][A-Z0-9]{1,15}$"
INSTITUTION_TYPES = Literal["bank", "e_wallet", "fintech", "brokerage", "cash", "crypto_wallet", "system", "other"]
CATEGORY_KINDS = Literal["income", "expense"]


class StrictCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=public_write_posting_semantics_schema_extra())
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
    opening_balance: Decimal = Field(
        default=Decimal("0"),
        description=(
            "Signed natural opening balance. For liability accounts, positive means initial debt "
            "and negative means initial overpayment; persisted postings still use debit/credit."
        ),
    )
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
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Positive draft business amount when known. Do not pass signed posting amounts; "
            "confirmed postings use positive debit/credit rows."
        ),
    )
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
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive business transfer amount. Do not pass signed posting amounts; "
            "persisted postings use positive debit/credit rows."
        ),
    )
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    from_account_id: str = Field(
        description=(
            "Source account for the transfer. The source is credited; if it is a liability, "
            "that increases the liability balance."
        )
    )
    to_account_id: str = Field(
        description=(
            "Target account for the transfer. The target is debited; asset-to-credit-card-liability "
            "transfers are repayments that decrease outstanding debt."
        )
    )
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    category_id: str | None = None
    counterparty: str | None = Field(default=None, min_length=1, max_length=120)


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
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Positive recurring item amount. Do not pass signed posting amounts; "
            "generated postings use positive debit/credit rows."
        ),
    )
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
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Positive recurring item amount. Do not pass signed posting amounts; "
            "generated postings use positive debit/credit rows."
        ),
    )
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
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive expense amount. Do not pass signed posting amounts; persisted postings use "
            "positive debit/credit rows."
        ),
    )
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    from_account_id: str = Field(
        description=(
            "Funding account for the expense. If this is a credit-card liability account, the "
            "positive expense credits the liability and increases outstanding debt."
        )
    )
    category_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    counterparty: str | None = Field(default=None, min_length=1, max_length=120)


class RecordIncomeCommand(StrictCommand):
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive income amount. Do not pass signed posting amounts; persisted postings use "
            "positive debit/credit rows."
        ),
    )
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    to_account_id: str
    category_id: str
    purpose: str = Field(min_length=1, max_length=256)
    memo: str = Field(default="", max_length=256)
    counterparty: str | None = Field(default=None, min_length=1, max_length=120)


class UpdateCreditCardProfileCommand(StrictCommand):
    credit_limit: Decimal | None = Field(
        default=None,
        ge=0,
        description="Non-negative profile credit limit. This is not a ledger posting amount.",
    )
    available_credit: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional provider-reported available credit metadata. This is not a ledger posting amount "
            "or natural liability balance; read derived_available_credit for ledger-derived availability."
        ),
    )
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    annual_fee: Decimal | None = Field(
        default=None,
        ge=0,
        description="Non-negative profile annual fee metadata. This is not automatically posted as a ledger expense.",
    )


class BalanceAdjustmentCommand(StrictCommand):
    account_id: str
    amount: Decimal = Field(
        description=(
            "Signed natural balance delta. For liability accounts, positive increases debt "
            "and negative decreases debt or creates overpayment; persisted postings still use debit/credit."
        )
    )
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
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive investment event amount. Do not pass signed posting amounts; "
            "persisted postings use positive debit/credit rows."
        ),
    )
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    memo: str = Field(default="", max_length=256)
    units: Decimal | None = Field(
        default=None,
        gt=0,
        description="Optional positive investment units quantity. This is not a ledger posting amount.",
    )
    nav: Decimal | None = Field(
        default=None,
        gt=0,
        description="Optional positive net asset value per unit. This is not a ledger posting amount.",
    )
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
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive fund allocation amount. Do not pass signed posting amounts; "
            "persisted postings use positive debit/credit rows."
        ),
    )
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    expected_version: int = Field(ge=1)
    memo: str = Field(default="", max_length=256)


class FundSpendCommand(StrictCommand):
    fund_id: str
    expense_account_id: str
    amount: Decimal = Field(
        gt=0,
        description=(
            "Positive fund spend amount. Do not pass signed posting amounts; "
            "persisted postings use positive debit/credit rows."
        ),
    )
    currency: str = Field(default="CNY", pattern=ASSET_CODE_PATTERN)
    expected_version: int = Field(ge=1)
    memo: str = Field(default="", max_length=256)


class ReverseTransactionCommand(StrictCommand):
    transaction_id: str
    memo: str = Field(min_length=1, max_length=256)


class ReclassifyTransactionCommand(StrictCommand):
    transaction_id: str
    category_id: str
    line_id: str | None = None
    memo: str = Field(default="", max_length=256)


class PostingSemanticsRewriteCommand(StrictCommand):
    pass


class PostingSemanticsReviewDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=posting_semantics_review_decision_schema_extra())

    record_ref: str | None = Field(default=None, min_length=1)
    transaction_id: str | None = Field(default=None, min_length=1)
    position: int = Field(ge=0)
    account_id: str = Field(min_length=1)
    currency: str = Field(pattern=ASSET_CODE_PATTERN)
    legacy_amount: str = Field(min_length=1)
    action: Literal["confirm_as_outstanding_liability", "confirm_as_liability_reduction_or_overpayment"]

    @field_validator("position", mode="before")
    @classmethod
    def validate_position(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("position must be a non-negative integer")
        return value

    @field_validator("legacy_amount")
    @classmethod
    def validate_legacy_amount(cls, value: str) -> str:
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("legacy_amount must be a decimal string") from exc
        if amount == Decimal("0"):
            raise ValueError("legacy_amount must not be zero")
        return value

    @model_validator(mode="after")
    def require_record_reference(self):
        if not self.record_ref and not self.transaction_id:
            raise ValueError("record_ref or transaction_id is required")
        if self.record_ref and self.transaction_id and self.record_ref != self.transaction_id:
            raise ValueError("record_ref and transaction_id must match when both are provided")
        return self


class PostingSemanticsReviewResolutionsCommand(StrictCommand):
    decisions: list[PostingSemanticsReviewDecisionCommand] = Field(min_length=1)


from .credential_commands import IssueCredentialCommand, RevokeCredentialByIdCommand, RevokeCredentialCommand


class ReconciliationActionCommand(StrictCommand):
    summary: str = Field(min_length=1, max_length=500)
