from __future__ import annotations

from enum import Enum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import model_validator

from ..journal.events import (
    FinancialExternalReference,
    JournalPostingFact,
)
from ..journal.models import PostingSide
from ..privacy import EventContract, validate_ordered_records


class CreditCardIntent(str, Enum):
    CHARGE = "charge"
    PAYMENT = "payment"
    REFUND = "refund"
    FEE = "fee"

    @property
    def transaction_kind(self) -> str:
        return f"credit_card_{self.value}"


class CreditCardTransactionRecorded(EventContract):
    """An immutable semantic credit-card fact with canonical journal legs."""

    event_type: ClassVar[str] = "CreditCardTransactionRecorded"

    intent: CreditCardIntent
    transaction_id: UUID
    card_account_id: UUID
    counter_account_id: UUID
    original_transaction_id: UUID | None = None
    postings: tuple[JournalPostingFact, ...]
    description_ref: UUID | None = None
    external_references: tuple[FinancialExternalReference, ...] = ()

    @model_validator(mode="after")
    def validate_semantic_postings(self) -> Self:
        validate_ordered_records(
            self.postings,
            minimum=2,
            unique_fields=("posting_id",),
        )
        if len(self.postings) != 2:
            raise ValueError("credit-card events require exactly two postings")
        if self.card_account_id == self.counter_account_id:
            raise ValueError("credit-card and counter accounts must differ")
        if self.intent is CreditCardIntent.REFUND:
            if self.original_transaction_id is None:
                raise ValueError("a refund requires its original charge transaction")
            if self.original_transaction_id == self.transaction_id:
                raise ValueError("a refund cannot reference itself")
        elif self.original_transaction_id is not None:
            raise ValueError("only a refund may reference an original transaction")

        first, second = self.postings
        if first.asset_code != second.asset_code or first.units != second.units:
            raise ValueError("credit-card postings must use one equal asset amount")
        if self.intent in {CreditCardIntent.CHARGE, CreditCardIntent.FEE}:
            expected = (
                (self.counter_account_id, PostingSide.DEBIT),
                (self.card_account_id, PostingSide.CREDIT),
            )
        else:
            expected = (
                (self.card_account_id, PostingSide.DEBIT),
                (self.counter_account_id, PostingSide.CREDIT),
            )
        actual = tuple((posting.account_id, posting.side) for posting in self.postings)
        if actual != expected:
            raise ValueError("credit-card postings do not match their semantic intent")

        reference_keys = tuple(
            (reference.provider_code, reference.kind)
            for reference in self.external_references
        )
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("external reference provider/kind pairs must be unique")
        return self


__all__ = ["CreditCardIntent", "CreditCardTransactionRecorded"]
