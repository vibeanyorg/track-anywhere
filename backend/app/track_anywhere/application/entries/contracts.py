from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, TypeAlias, runtime_checkable
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from ...domain.privacy import AssetCode, FrozenContract


_PLAIN_DECIMAL = re.compile(r"^[0-9]+(?:\.[0-9]+)?$", flags=re.ASCII)
_ACCOUNT_SUBTYPE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", flags=re.ASCII)
_PROVIDER_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$", flags=re.ASCII)
_EXTERNAL_REFERENCE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    flags=re.ASCII,
)

NonBlankText = Annotated[StrictStr, Field(min_length=1, max_length=512)]
OpaqueToken = Annotated[StrictStr, Field(min_length=32, max_length=512)]


class EntryContract(FrozenContract):
    """Strict and immutable boundary shared by every entry adapter."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        use_enum_values=False,
    )


class MoneyDenomination(StrEnum):
    ASSET_UNIT = "asset_unit"
    MINOR_UNIT = "minor_unit"


class MoneyInput(EntryContract):
    """Exact user-facing amount; never canonical ledger units."""

    value: StrictStr = Field(min_length=1, max_length=96)
    denomination: MoneyDenomination = MoneyDenomination.ASSET_UNIT
    asset_code: AssetCode
    source_text: StrictStr = Field(min_length=1, max_length=256, repr=False)

    @field_validator("value")
    @classmethod
    def validate_positive_plain_decimal(cls, value: str) -> str:
        if (
            _PLAIN_DECIMAL.fullmatch(value) is None
            or value.rstrip("0").rstrip(".") in {"", "0"}
        ):
            raise ValueError("value must be a positive unsigned plain-decimal string")
        return value

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_text must be nonblank")
        return value


class BalanceInput(EntryContract):
    """Exact non-negative observed balance, including a legitimate zero."""

    value: StrictStr = Field(min_length=1, max_length=96)
    denomination: MoneyDenomination = MoneyDenomination.ASSET_UNIT
    asset_code: AssetCode
    source_text: StrictStr = Field(min_length=1, max_length=256, repr=False)

    @field_validator("value")
    @classmethod
    def validate_non_negative_plain_decimal(cls, value: str) -> str:
        if _PLAIN_DECIMAL.fullmatch(value) is None:
            raise ValueError("value must be a non-negative plain-decimal string")
        return value

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_text must be nonblank")
        return value


class AccountRef(EntryContract):
    """An exact account ID or a human query with optional disambiguators."""

    account_id: UUID | None = None
    query: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    last4: StrictStr | None = Field(default=None, pattern=r"^[0-9]{4}$")
    subtype: StrictStr | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("account query must be nonblank")
        return normalized

    @field_validator("subtype")
    @classmethod
    def validate_subtype(cls, value: str | None) -> str | None:
        if value is not None and _ACCOUNT_SUBTYPE.fullmatch(value) is None:
            raise ValueError("account subtype is invalid")
        return value

    @model_validator(mode="after")
    def validate_selector(self) -> AccountRef:
        if (self.account_id is None) == (self.query is None):
            raise ValueError("account reference requires exactly one selector")
        if self.account_id is not None and (
            self.last4 is not None or self.subtype is not None
        ):
            raise ValueError("account query hints require a query selector")
        return self


class CategoryRef(EntryContract):
    """An exact category ID, complete path, or human query."""

    category_id: UUID | None = None
    path: tuple[StrictStr, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=16,
    )
    query: StrictStr | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("path")
    @classmethod
    def normalize_path(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized = tuple(part.strip() for part in value)
        if any(not part or len(part) > 128 for part in normalized):
            raise ValueError("category path components must be nonblank and bounded")
        return normalized

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("category query must be nonblank")
        return normalized

    @model_validator(mode="after")
    def validate_selector(self) -> CategoryRef:
        if sum(
            selector is not None
            for selector in (self.category_id, self.path, self.query)
        ) != 1:
            raise ValueError("category reference requires exactly one selector")
        return self


class ExternalReferenceKind(StrEnum):
    PROVIDER_TRANSACTION = "provider_transaction"
    PROVIDER_ORDER = "provider_order"
    IMPORT_RECORD = "import_record"


class ExternalReferenceInput(EntryContract):
    provider_code: StrictStr = Field(min_length=1, max_length=32)
    kind: ExternalReferenceKind
    reference: StrictStr = Field(min_length=1, max_length=128, repr=False)

    @field_validator("provider_code")
    @classmethod
    def validate_provider_code(cls, value: str) -> str:
        if _PROVIDER_CODE.fullmatch(value) is None:
            raise ValueError("provider_code is invalid")
        return value

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        if _EXTERNAL_REFERENCE.fullmatch(value) is None:
            raise ValueError("external reference is invalid")
        return value


class EntryNarrativeInput(EntryContract):
    """Private text destined for encrypted protected content, never events."""

    merchant: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        repr=False,
    )
    channel: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        repr=False,
    )
    note: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        repr=False,
    )
    external_reference: ExternalReferenceInput | None = Field(
        default=None,
        repr=False,
    )
    gross_amount: MoneyInput | None = Field(default=None, repr=False)
    discount_amount: MoneyInput | None = Field(default=None, repr=False)

    @field_validator("merchant", "channel", "note")
    @classmethod
    def validate_private_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("private narrative text must be nonblank")
        return value


class CategoryAllocationInput(EntryContract):
    category: CategoryRef
    amount: MoneyInput


class _TimedEntryInput(EntryContract):
    occurred_at: AwareDatetime
    narrative: EntryNarrativeInput | None = Field(default=None, repr=False)


class _CategorizedEntryInput(_TimedEntryInput):
    amount: MoneyInput
    category: CategoryRef | None = None
    category_allocations: tuple[CategoryAllocationInput, ...] = Field(
        default=(),
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_classification_shape(self) -> _CategorizedEntryInput:
        if (self.category is None) == (len(self.category_allocations) == 0):
            raise ValueError(
                "entry requires either one category or category allocations"
            )
        return self


class ExpenseEntryInput(_CategorizedEntryInput):
    kind: Literal["expense"] = "expense"
    source_account: AccountRef


class IncomeEntryInput(_CategorizedEntryInput):
    kind: Literal["income"] = "income"
    destination_account: AccountRef


class TransferEntryInput(_TimedEntryInput):
    kind: Literal["transfer"] = "transfer"
    amount: MoneyInput
    source_account: AccountRef
    destination_account: AccountRef

    @model_validator(mode="after")
    def validate_distinct_accounts(self) -> TransferEntryInput:
        if self.source_account == self.destination_account:
            raise ValueError("transfer accounts must be distinct")
        return self


class CreditCardPaymentEntryInput(_TimedEntryInput):
    kind: Literal["credit_card_payment"] = "credit_card_payment"
    amount: MoneyInput
    funding_account: AccountRef
    card_account: AccountRef

    @model_validator(mode="after")
    def validate_distinct_accounts(self) -> CreditCardPaymentEntryInput:
        if self.funding_account == self.card_account:
            raise ValueError("credit-card payment accounts must be distinct")
        return self


class RefundEntryInput(_TimedEntryInput):
    kind: Literal["refund"] = "refund"
    original_transaction_id: UUID
    amount: MoneyInput | None = None
    category_allocations: tuple[CategoryAllocationInput, ...] = Field(
        default=(),
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_refund_allocations(self) -> RefundEntryInput:
        if self.category_allocations and self.amount is None:
            raise ValueError("refund allocations require an explicit partial amount")
        return self


class AdjustmentEntryInput(_TimedEntryInput):
    kind: Literal["adjustment"] = "adjustment"
    account: AccountRef
    actual_balance: BalanceInput


EverydayEntryInput: TypeAlias = Annotated[
    ExpenseEntryInput
    | IncomeEntryInput
    | TransferEntryInput
    | CreditCardPaymentEntryInput
    | RefundEntryInput
    | AdjustmentEntryInput,
    Field(discriminator="kind"),
]


class PreparedEntryStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    DUPLICATE_SUSPECTED = "duplicate_suspected"
    UNSUPPORTED = "unsupported"


class EntryWarningCode(StrEnum):
    AMOUNT_SOURCE_MISMATCH = "amount_source_mismatch"
    UNUSUAL_AMOUNT = "unusual_amount"
    PARTIAL_REFUND = "partial_refund"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


class ClarificationCode(StrEnum):
    ACCOUNT_SELECTION = "account_selection"
    CATEGORY_SELECTION = "category_selection"
    REFUND_ALLOCATION = "refund_allocation"
    DUPLICATE_CONFIRMATION = "duplicate_confirmation"
    UNSUPPORTED_DETAIL = "unsupported_detail"


class PreviewMoney(EntryContract):
    value: StrictStr = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$", max_length=96)
    asset_code: AssetCode
    display: NonBlankText


class PreviewAccount(EntryContract):
    role: Literal["source", "destination", "funding", "card", "adjusted"]
    display_name: NonBlankText


class EntryPreview(EntryContract):
    kind: Literal[
        "expense",
        "income",
        "transfer",
        "credit_card_payment",
        "refund",
        "adjustment",
    ]
    summary: NonBlankText
    amount: PreviewMoney
    occurred_at: AwareDatetime
    accounts: tuple[PreviewAccount, ...] = Field(default=(), max_length=4)
    category_paths: tuple[tuple[StrictStr, ...], ...] = Field(
        default=(),
        max_length=64,
    )

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("preview summary must be nonblank")
        return value

    @field_validator("category_paths")
    @classmethod
    def validate_category_paths(
        cls,
        value: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        if any(not path or any(not part.strip() for part in path) for path in value):
            raise ValueError("preview category paths must be nonblank")
        return value


class ResolvedEntryReferences(EntryContract):
    source_account_id: UUID | None = None
    destination_account_id: UUID | None = None
    funding_account_id: UUID | None = None
    card_account_id: UUID | None = None
    adjusted_account_id: UUID | None = None
    category_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    category_version_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    original_transaction_id: UUID | None = None

    @model_validator(mode="after")
    def validate_category_pairs(self) -> ResolvedEntryReferences:
        if len(self.category_ids) != len(self.category_version_ids):
            raise ValueError("resolved category identities must be paired")
        return self


class EntryWarning(EntryContract):
    code: EntryWarningCode
    message: NonBlankText
    field: StrictStr | None = Field(default=None, min_length=1, max_length=128)


class ClarificationChoice(EntryContract):
    choice_id: StrictStr = Field(min_length=1, max_length=128)
    label: NonBlankText
    resolved_id: UUID | None = None


class Clarification(EntryContract):
    code: ClarificationCode
    field: StrictStr = Field(min_length=1, max_length=128)
    prompt: NonBlankText
    choices: tuple[ClarificationChoice, ...] = Field(
        default=(),
        max_length=64,
    )


class PreparedEntry(EntryContract):
    intent_id: UUID
    status: PreparedEntryStatus
    commit_token: OpaqueToken | None = Field(default=None, repr=False)
    expires_at: AwareDatetime
    preview: EntryPreview
    resolved: ResolvedEntryReferences
    warnings: tuple[EntryWarning, ...] = Field(default=(), max_length=64)
    clarifications: tuple[Clarification, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_state(self) -> PreparedEntry:
        if self.status is PreparedEntryStatus.READY:
            if self.commit_token is None:
                raise ValueError("ready entry requires a commit token")
            if self.clarifications:
                raise ValueError("ready entry cannot require clarification")
        elif self.commit_token is not None:
            raise ValueError("only a ready entry may expose a commit token")

        if self.status in {
            PreparedEntryStatus.NEEDS_CLARIFICATION,
            PreparedEntryStatus.DUPLICATE_SUSPECTED,
        } and not self.clarifications:
            raise ValueError("entry status requires structured clarification")
        return self


class CommitEntryInput(EntryContract):
    intent_id: UUID
    commit_token: OpaqueToken = Field(repr=False)
    request_id: UUID


class CommittedEntry(EntryContract):
    status: Literal["committed"] = "committed"
    intent_id: UUID
    request_id: UUID
    transaction_id: UUID
    committed_at: AwareDatetime
    replayed: StrictBool = False
    preview: EntryPreview


@runtime_checkable
class EverydayEntryService(Protocol):
    """Request-scoped service used unchanged by REST, MCP, and friendly CLI."""

    def prepare(
        self,
        *,
        book_id: UUID,
        entry: EverydayEntryInput,
    ) -> PreparedEntry: ...

    def commit(
        self,
        *,
        book_id: UUID,
        command: CommitEntryInput,
    ) -> CommittedEntry: ...


__all__ = [
    "AccountRef",
    "AdjustmentEntryInput",
    "BalanceInput",
    "CategoryAllocationInput",
    "CategoryRef",
    "Clarification",
    "ClarificationChoice",
    "ClarificationCode",
    "CommitEntryInput",
    "CommittedEntry",
    "CreditCardPaymentEntryInput",
    "EntryNarrativeInput",
    "EntryPreview",
    "EntryWarning",
    "EntryWarningCode",
    "EverydayEntryInput",
    "EverydayEntryService",
    "ExpenseEntryInput",
    "ExternalReferenceInput",
    "ExternalReferenceKind",
    "IncomeEntryInput",
    "MoneyDenomination",
    "MoneyInput",
    "PreparedEntry",
    "PreparedEntryStatus",
    "PreviewAccount",
    "PreviewMoney",
    "RefundEntryInput",
    "ResolvedEntryReferences",
    "TransferEntryInput",
]
