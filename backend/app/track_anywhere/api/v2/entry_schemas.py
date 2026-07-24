from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ...application.entries.contracts import (
    AdjustmentEntryInput,
    CommitEntryInput,
    CommittedEntry,
    CreditCardPaymentEntryInput,
    ExpenseEntryInput,
    IncomeEntryInput,
    PreparedEntry,
    RefundEntryInput,
    TransferEntryInput,
)
from ...queries.everyday_entries import (
    AccountDisplay,
    AssetUnitAmount,
    CategoryAllocationView,
    EverydayEntryView,
    NarrativeView,
    RawJournalReference,
)


PrepareEntryRequest = Annotated[
    ExpenseEntryInput
    | IncomeEntryInput
    | TransferEntryInput
    | CreditCardPaymentEntryInput
    | RefundEntryInput
    | AdjustmentEntryInput,
    Field(discriminator="kind"),
]


class EntryErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    field: str | None = None
    retryable: bool = False


class EntryErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: EntryErrorDetail


class AssetUnitAmountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    value: str
    asset_code: str
    scale: int


class AccountDisplayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    account_id: UUID
    display_name: str


class CategoryAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    category_id: UUID
    category_version_id: UUID
    path: tuple[str, ...]
    amount: AssetUnitAmountResponse


class NarrativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    status: str
    merchant: str | None = None
    channel: str | None = None


class RawJournalReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    transaction_id: UUID
    book_position: int
    transaction_kind: str


class EverydayEntryReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    entry_id: UUID
    transaction_id: UUID
    kind: str
    amount: AssetUnitAmountResponse | None
    amount_availability: str
    source_account: AccountDisplayResponse | None
    target_account: AccountDisplayResponse | None
    payment_account: AccountDisplayResponse | None
    account_display_availability: str
    category_allocations: tuple[CategoryAllocationResponse, ...]
    category_availability: str
    occurred_at: datetime
    original_transaction_id: UUID | None
    reversed_by_transaction_id: UUID | None
    reverses_transaction_id: UUID | None
    relationship_availability: str
    narrative: NarrativeResponse
    raw_journal: RawJournalReferenceResponse

    @classmethod
    def from_view(cls, view: EverydayEntryView) -> EverydayEntryReceiptResponse:
        return cls.model_validate(view)


__all__ = [
    "CommitEntryInput",
    "CommittedEntry",
    "EntryErrorResponse",
    "EverydayEntryReceiptResponse",
    "PrepareEntryRequest",
    "PreparedEntry",
]
