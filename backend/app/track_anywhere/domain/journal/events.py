from __future__ import annotations

from enum import Enum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from ..privacy import (
    AssetCode,
    CanonicalEventHash,
    CanonicalUnits,
    EventContract,
    ExternalReferenceValue,
    FrozenContract,
    ProviderCode,
    validate_ordered_records,
)
from .models import PostingSide, TransactionKind


class ExternalReferenceKind(str, Enum):
    PROVIDER_TRANSACTION = "provider_transaction"
    BANK_TRANSACTION = "bank_transaction"
    CARD_TRANSACTION = "card_transaction"
    BROKER_TRADE = "broker_trade"


class ReversalReasonCode(str, Enum):
    USER_CORRECTION = "user_correction"
    DUPLICATE = "duplicate"
    IMPORT_CORRECTION = "import_correction"
    PROVIDER_REVERSAL = "provider_reversal"


class JournalPostingFact(FrozenContract):
    posting_id: UUID
    position: StrictInt = Field(ge=0)
    account_id: UUID
    asset_code: AssetCode
    side: PostingSide
    units: CanonicalUnits


class FinancialExternalReference(FrozenContract):
    provider_code: ProviderCode
    kind: ExternalReferenceKind
    reference: ExternalReferenceValue


class JournalTransactionPosted(EventContract):
    event_type: ClassVar[str] = "JournalTransactionPosted"

    transaction_id: UUID
    kind: TransactionKind
    original_transaction_id: UUID | None = None
    postings: tuple[JournalPostingFact, ...]
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()

    @model_validator(mode="after")
    def validate_complete_postings(self) -> Self:
        validate_ordered_records(
            self.postings,
            minimum=2,
            unique_fields=("posting_id",),
        )
        reference_keys = [
            (reference.provider_code, reference.kind)
            for reference in self.external_references
        ]
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("external reference provider/kind pairs must be unique")
        if self.kind is TransactionKind.REFUND:
            if self.original_transaction_id is None:
                raise ValueError("a refund requires its original transaction")
            if self.original_transaction_id == self.transaction_id:
                raise ValueError("a refund cannot reference itself")
        elif self.original_transaction_id is not None:
            raise ValueError("only a refund may reference an original transaction")
        return self


class JournalTransactionReversed(EventContract):
    event_type: ClassVar[str] = "JournalTransactionReversed"

    reversal_transaction_id: UUID
    reverses_transaction_id: UUID
    original_event_id: UUID
    original_event_hash: CanonicalEventHash
    reason_code: ReversalReasonCode
    inverse_postings: tuple[JournalPostingFact, ...]
    description_ref: UUID | None = None

    @model_validator(mode="after")
    def validate_complete_inverse_postings(self) -> Self:
        validate_ordered_records(
            self.inverse_postings,
            minimum=2,
            unique_fields=("posting_id",),
        )
        if self.reversal_transaction_id == self.reverses_transaction_id:
            raise ValueError(
                "a reversal transaction must identify the original transaction"
            )
        return self


class FinancialExternalReferenceCorrected(EventContract):
    event_type: ClassVar[str] = "FinancialExternalReferenceCorrected"

    transaction_id: UUID
    provider_code: ProviderCode
    reference_kind: ExternalReferenceKind
    previous_reference: ExternalReferenceValue | None = None
    corrected_reference: ExternalReferenceValue

    @model_validator(mode="after")
    def validate_actual_correction(self) -> Self:
        if self.previous_reference == self.corrected_reference:
            raise ValueError("corrected reference must differ from the previous value")
        return self
