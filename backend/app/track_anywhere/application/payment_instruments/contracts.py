from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
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


class PaymentInstrumentBindingView(FrozenContract):
    binding_id: UUID
    asset_code: str
    settlement_policy: SettlementPolicy
    settlement_account_id: UUID
    settlement_account_name: str
    binding_role: BindingRole
    status: str
    effective_from: datetime
    effective_to: datetime | None


class PaymentInstrumentMutation(FrozenContract):
    book_id: UUID
    request_id: UUID
    instrument_id: UUID
    operation: Literal["update", "close", "reopen", "add_binding", "close_binding"]
    current_name: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    network: CardNetwork | None = None
    provider_code: StrictStr | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$"
    )
    form_factor: CardFormFactor | None = None
    last4: StrictStr | None = Field(default=None, pattern=r"^[0-9]{4}$")
    binding_id: UUID | None = None
    settlement_account_id: UUID | None = None
    asset_code: StrictStr | None = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9._-]{0,15}$"
    )
    settlement_policy: SettlementPolicy | None = None
    effective_from: AwareDatetime | None = None
    effective_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> PaymentInstrumentMutation:
        allowed = {
            "update": {
                "current_name",
                "network",
                "provider_code",
                "form_factor",
                "last4",
            },
            "close": set(),
            "reopen": set(),
            "add_binding": {
                "settlement_account_id",
                "asset_code",
                "settlement_policy",
                "effective_from",
            },
            "close_binding": {"binding_id", "effective_to"},
        }[self.operation]
        supplied = self.model_fields_set - {
            "book_id",
            "request_id",
            "instrument_id",
            "operation",
        }
        if supplied - allowed or (self.operation == "update" and not supplied):
            raise ValueError("invalid fields for payment instrument operation")
        if self.operation in {"add_binding", "close_binding"} and any(
            getattr(self, f) is None for f in allowed
        ):
            raise ValueError("missing required binding fields")
        if any(getattr(self, f) is None for f in supplied - {"last4"}):
            raise ValueError("only last4 can be cleared")
        if self.current_name is not None and not self.current_name.strip():
            raise ValueError("current_name must be nonblank")
        return self


class PaymentInstrumentView(FrozenContract):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    book_id: UUID
    instrument_id: UUID
    binding_id: UUID | None
    instrument_kind: str
    current_name: str
    form_factor: CardFormFactor
    network: CardNetwork
    provider_code: str
    settlement_policy: SettlementPolicy | None
    settlement_account_id: UUID | None
    asset_code: str | None
    binding_role: BindingRole | None
    last4: str | None
    status: str
    effective_from: datetime
    effective_to: datetime | None
    bindings: tuple[PaymentInstrumentBindingView, ...] = ()
    version: int = 1


__all__ = [
    "BindingRole",
    "CardFormFactor",
    "CardNetwork",
    "CreatePaymentInstrument",
    "PaymentInstrumentRef",
    "PaymentInstrumentView",
    "SettlementPolicy",
]
