from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from ...domain.privacy import FrozenContract


class CardFormFactor(StrEnum):
    VIRTUAL = "virtual"
    PHYSICAL = "physical"
    SINGLE_USE = "single_use"


class CardNetwork(StrEnum):
    MASTERCARD = "mastercard"
    VISA = "visa"
    AMEX = "amex"
    UNIONPAY = "unionpay"
    OTHER = "other"


class SettlementPolicy(StrEnum):
    """How a card purchase reaches the ledger."""

    IMMEDIATE = "immediate"
    PREPAID = "prepaid"
    STATEMENT = "statement"


class BindingRole(StrEnum):
    FUNDING_ASSET = "funding_asset"
    CARD_LIABILITY = "card_liability"


class PaymentInstrumentRef(FrozenContract):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    instrument_id: UUID | None = None
    query: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    last4: StrictStr | None = Field(default=None, pattern=r"^[0-9]{4}$")
    provider_code: StrictStr | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{0,31}$",
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("payment instrument query must be nonblank")
        return normalized

    @model_validator(mode="after")
    def validate_selector(self) -> PaymentInstrumentRef:
        if (self.instrument_id is None) == (self.query is None):
            raise ValueError(
                "payment instrument reference requires exactly one selector"
            )
        if self.instrument_id is not None and (
            self.last4 is not None or self.provider_code is not None
        ):
            raise ValueError("payment instrument query hints require a query selector")
        return self


class CreatePaymentInstrument(FrozenContract):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    book_id: UUID
    instrument_id: UUID
    binding_id: UUID
    current_name: StrictStr = Field(min_length=1, max_length=512)
    form_factor: CardFormFactor
    network: CardNetwork
    provider_code: StrictStr = Field(
        pattern=r"^[a-z][a-z0-9_-]{0,31}$",
        max_length=32,
    )
    settlement_policy: SettlementPolicy
    settlement_account_id: UUID
    asset_code: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9._-]{0,15}$")
    last4: StrictStr | None = Field(default=None, pattern=r"^[0-9]{4}$")
    effective_from: AwareDatetime

    @field_validator("current_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("current_name must be nonblank")
        return normalized


class PaymentInstrumentView(FrozenContract):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    book_id: UUID
    instrument_id: UUID
    binding_id: UUID
    instrument_kind: str
    current_name: str
    form_factor: CardFormFactor
    network: CardNetwork
    provider_code: str
    settlement_policy: SettlementPolicy
    settlement_account_id: UUID
    asset_code: str
    binding_role: BindingRole
    last4: str | None
    status: str
    effective_from: datetime
    effective_to: datetime | None


__all__ = [
    "BindingRole",
    "CardFormFactor",
    "CardNetwork",
    "CreatePaymentInstrument",
    "PaymentInstrumentRef",
    "PaymentInstrumentView",
    "SettlementPolicy",
]
