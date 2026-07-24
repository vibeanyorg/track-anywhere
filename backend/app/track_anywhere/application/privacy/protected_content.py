from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field, StrictBytes, StrictStr, field_validator

from ...domain.privacy import AssetCode, FrozenContract


ProtectedContentKind: TypeAlias = Literal[
    "transaction_description",
    "transaction_narrative_v2",
    "import_archive",
]


class TransactionDescription(FrozenContract):
    purpose: StrictStr | None = Field(repr=False)
    transaction_memo: StrictStr | None = Field(repr=False)
    line_memos: tuple[StrictStr | None, ...] = Field(repr=False)


class NarrativeMoney(FrozenContract):
    value: StrictStr = Field(
        pattern=r"^[0-9]+(?:\.[0-9]+)?$",
        max_length=96,
        repr=False,
    )
    asset_code: AssetCode


class NarrativeExternalReference(FrozenContract):
    provider_code: StrictStr = Field(
        pattern=r"^[a-z][a-z0-9_-]{0,31}$",
        max_length=32,
    )
    kind: Literal["provider_transaction", "provider_order", "import_record"]
    reference: StrictStr = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
        min_length=1,
        max_length=128,
        repr=False,
    )


class TransactionNarrativeV2(FrozenContract):
    """Canonical encrypted narrative contract; every optional key is explicit."""

    contract_version: Literal[2] = 2
    source_text: StrictStr = Field(
        min_length=1,
        max_length=256,
        repr=False,
    )
    purpose: StrictStr | None = Field(default=None, repr=False)
    transaction_memo: StrictStr | None = Field(default=None, repr=False)
    line_memos: tuple[StrictStr | None, ...] = Field(default=(), repr=False)
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
    external_reference: NarrativeExternalReference | None = Field(
        default=None,
        repr=False,
    )
    gross_amount: NarrativeMoney | None = Field(default=None, repr=False)
    discount_amount: NarrativeMoney | None = Field(default=None, repr=False)
    net_amount: NarrativeMoney | None = Field(default=None, repr=False)

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_text must be nonblank")
        return value


class TransactionNarrative(FrozenContract):
    """Version-neutral query shape produced by v1/v2 upcasting."""

    source_text: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        repr=False,
    )
    purpose: StrictStr | None = Field(default=None, repr=False)
    transaction_memo: StrictStr | None = Field(default=None, repr=False)
    line_memos: tuple[StrictStr | None, ...] = Field(default=(), repr=False)
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
    external_reference: NarrativeExternalReference | None = Field(
        default=None,
        repr=False,
    )
    gross_amount: NarrativeMoney | None = Field(default=None, repr=False)
    discount_amount: NarrativeMoney | None = Field(default=None, repr=False)
    net_amount: NarrativeMoney | None = Field(default=None, repr=False)

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("source_text must be nonblank")
        return value


def upcast_transaction_description(
    value: TransactionDescription | TransactionNarrativeV2,
) -> TransactionNarrative:
    if type(value) is TransactionDescription:
        return TransactionNarrative(
            source_text=None,
            purpose=value.purpose,
            transaction_memo=value.transaction_memo,
            line_memos=value.line_memos,
        )
    if type(value) is TransactionNarrativeV2:
        return TransactionNarrative(
            source_text=value.source_text,
            purpose=value.purpose,
            transaction_memo=value.transaction_memo,
            line_memos=value.line_memos,
            merchant=value.merchant,
            channel=value.channel,
            note=value.note,
            external_reference=value.external_reference,
            gross_amount=value.gross_amount,
            discount_amount=value.discount_amount,
            net_amount=value.net_amount,
        )
    raise TypeError("transaction narrative contract is invalid")


class ProtectedContentEnvelope(FrozenContract):
    kind: ProtectedContentKind
    canonical_plaintext: StrictBytes = Field(repr=False)


__all__ = [
    "ProtectedContentEnvelope",
    "ProtectedContentKind",
    "NarrativeExternalReference",
    "NarrativeMoney",
    "TransactionNarrative",
    "TransactionNarrativeV2",
    "TransactionDescription",
    "upcast_transaction_description",
]
