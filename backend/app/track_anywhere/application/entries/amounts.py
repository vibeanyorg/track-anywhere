from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ...domain.money import ScaledUnits
from .contracts import BalanceInput, MoneyDenomination, MoneyInput
from .errors import EntryErrorCode, EntryGatewayError


@dataclass(frozen=True, slots=True)
class EntryAsset:
    asset_code: str
    kind: str
    ledger_scale: int
    input_scale: int
    minor_unit_scale: int | None
    status: str = "active"

    def __post_init__(self) -> None:
        if (
            type(self.asset_code) is not str
            or not self.asset_code
            or self.asset_code != self.asset_code.upper()
        ):
            raise ValueError("asset_code must be a non-empty uppercase string")
        if type(self.kind) is not str or not self.kind:
            raise ValueError("asset kind must be a non-empty string")
        for name, value in (
            ("ledger_scale", self.ledger_scale),
            ("input_scale", self.input_scale),
        ):
            if type(value) is not int or not 0 <= value <= 30:
                raise ValueError(f"{name} must be an integer between 0 and 30")
        if self.input_scale > self.ledger_scale:
            raise ValueError("input_scale cannot exceed ledger_scale")
        if self.minor_unit_scale is not None and (
            type(self.minor_unit_scale) is not int
            or not 0 <= self.minor_unit_scale <= self.ledger_scale
        ):
            raise ValueError(
                "minor_unit_scale must be null or between zero and ledger_scale"
            )
        if type(self.status) is not str or not self.status:
            raise ValueError("asset status must be a non-empty string")


@dataclass(frozen=True, slots=True)
class NormalizedAmount:
    units: int
    ledger_scale: int
    asset_code: str

    @property
    def canonical_units(self) -> str:
        return str(self.units)

    def asset_unit_value(self) -> str:
        return ScaledUnits(self.units, self.ledger_scale).decode()


def normalize_amount(
    amount: MoneyInput | BalanceInput,
    *,
    asset: EntryAsset,
    allow_zero: bool = False,
) -> NormalizedAmount:
    if amount.asset_code != asset.asset_code:
        raise EntryGatewayError(
            EntryErrorCode.AMOUNT_INVALID,
            "amount asset does not match the selected asset policy",
            field="amount.asset_code",
        )
    if asset.status != "active":
        raise EntryGatewayError(
            EntryErrorCode.DENOMINATION_UNSUPPORTED,
            "amount asset is unavailable",
            field="amount.asset_code",
        )
    validate_amount_source_consistency(amount)

    if amount.denomination is MoneyDenomination.ASSET_UNIT:
        scale = asset.ledger_scale
        max_input_scale = asset.input_scale
    else:
        if asset.kind != "fiat" or asset.minor_unit_scale is None:
            raise EntryGatewayError(
                EntryErrorCode.DENOMINATION_UNSUPPORTED,
                "minor_unit denomination is unsupported for this asset",
                field="amount.denomination",
            )
        scale = asset.ledger_scale - asset.minor_unit_scale
        max_input_scale = scale

    try:
        parsed = _parse_non_negative(
            amount.value,
            scale=scale,
            max_input_scale=max_input_scale,
            allow_zero=allow_zero,
        )
    except ValueError as exc:
        raise EntryGatewayError(
            EntryErrorCode.AMOUNT_INVALID,
            str(exc),
            field="amount.value",
        ) from None

    units = parsed
    if len(str(units)) > 38:
        raise EntryGatewayError(
            EntryErrorCode.AMOUNT_INVALID,
            "amount units must contain at most 38 digits",
            field="amount.value",
        )
    return NormalizedAmount(
        units=units,
        ledger_scale=asset.ledger_scale,
        asset_code=asset.asset_code,
    )


def validate_amount_source_consistency(
    amount: MoneyInput | BalanceInput,
) -> None:
    """Reject only explicit denomination contradictions; never rewrite the value."""

    source = unicodedata.normalize("NFKC", amount.source_text).casefold().strip()
    explicit_minor = (
        source.endswith("分")
        or source.endswith("cent")
        or source.endswith("cents")
        or "minor_unit" in source
    )
    explicit_asset = (
        "asset_unit" in source
        or (
            amount.asset_code == "CNY"
            and ("¥" in source or "￥" in source or source.endswith("元"))
        )
    )
    contradicted = (
        explicit_minor and amount.denomination is MoneyDenomination.ASSET_UNIT
    ) or (
        explicit_asset and amount.denomination is MoneyDenomination.MINOR_UNIT
    )
    if contradicted:
        raise EntryGatewayError(
            EntryErrorCode.AMOUNT_SOURCE_MISMATCH,
            "source text explicitly contradicts the amount denomination",
            field="amount.denomination",
        )


def _parse_non_negative(
    raw: str,
    *,
    scale: int,
    max_input_scale: int,
    allow_zero: bool,
) -> int:
    whole, separator, fraction = raw.partition(".")
    if not whole.isascii() or not whole.isdigit() or (
        separator and (not fraction.isascii() or not fraction.isdigit())
    ):
        raise ValueError("amount must be an unsigned plain-decimal string")
    if len(fraction) > max_input_scale:
        raise ValueError(
            f"amount exceeds the permitted input scale of {max_input_scale}"
        )
    digits = whole + fraction.ljust(scale, "0")
    units = int(digits)
    if units == 0 and not allow_zero:
        raise ValueError("amount units must be positive")
    if len(digits.lstrip("0")) > 38:
        raise ValueError("amount units must contain at most 38 digits")
    return units


__all__ = [
    "EntryAsset",
    "NormalizedAmount",
    "normalize_amount",
    "validate_amount_source_consistency",
]
