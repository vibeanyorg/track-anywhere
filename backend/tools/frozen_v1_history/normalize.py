from __future__ import annotations

from dataclasses import dataclass
import re


_HISTORICAL_DECIMAL = re.compile(
    r"^(?P<sign>-?)(?P<whole>0|[1-9][0-9]*)"
    r"(?:\.(?P<fraction>[0-9]+))?"
    r"(?:[eE](?P<exponent>[+-]?[0-9]+))?$",
    flags=re.ASCII,
)
_UNSIGNED_HISTORICAL_DECIMAL = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$",
    flags=re.ASCII,
)
_MAX_UNIT_DIGITS = 38
_MAX_SCALE = 30


@dataclass(frozen=True, slots=True)
class HistoricalAssetScale:
    ledger_scale: int
    input_scale: int
    display_scale: int

    def __post_init__(self) -> None:
        for value in (self.ledger_scale, self.input_scale, self.display_scale):
            if type(value) is not int or not 0 <= value <= _MAX_SCALE:
                raise ValueError("historical asset scale is outside its allowed range")
        if self.input_scale > self.ledger_scale or self.display_scale > self.ledger_scale:
            raise ValueError("input and display scales cannot exceed ledger scale")

    @classmethod
    def for_source(
        cls, *, asset_code: str, source_scale: int, source_display_scale: int
    ) -> HistoricalAssetScale:
        if type(asset_code) is not str or not asset_code:
            raise ValueError("asset code must be nonblank")
        ledger_scale = max(source_scale, 8) if asset_code == "USDT" else source_scale
        return cls(
            ledger_scale=ledger_scale,
            input_scale=source_scale,
            display_scale=source_display_scale,
        )


@dataclass(frozen=True, slots=True)
class ExactPostingAmount:
    side: str
    units: int

    def __post_init__(self) -> None:
        if self.side not in {"debit", "credit"}:
            raise ValueError("posting side must be debit or credit")
        if type(self.units) is not int or self.units <= 0:
            raise ValueError("posting units must be a positive integer")

    def as_tuple(self) -> tuple[str, int]:
        return self.side, self.units


def _validate_scale(ledger_scale: object) -> int:
    if type(ledger_scale) is not int or not 0 <= ledger_scale <= _MAX_SCALE:
        raise ValueError("ledger scale is outside its allowed range")
    return ledger_scale


def _signed_exact_units(amount: str, *, ledger_scale: int) -> int:
    if type(amount) is not str:
        raise TypeError("historical amount must be an exact decimal string")
    scale = _validate_scale(ledger_scale)
    match = _HISTORICAL_DECIMAL.fullmatch(amount)
    if match is None:
        raise ValueError("historical amount must be an exact decimal string")

    fraction = match.group("fraction") or ""
    coefficient = (match.group("whole") + fraction).lstrip("0")
    if not coefficient:
        raise ValueError("historical amount units must be positive")
    raw_exponent = match.group("exponent") or "0"
    exponent_digits = raw_exponent.lstrip("+-").lstrip("0")
    if len(exponent_digits) > 6:
        raise ValueError("historical amount exponent is outside the supported range")
    normalized_exponent = exponent_digits or "0"
    exponent = int(
        f"-{normalized_exponent}"
        if raw_exponent.startswith("-")
        else normalized_exponent
    )
    unit_exponent = exponent - len(fraction) + scale

    if unit_exponent >= 0:
        if len(coefficient) + unit_exponent > _MAX_UNIT_DIGITS:
            raise ValueError("historical amount exceeds the ledger unit bound")
        magnitude = int(coefficient) * (10**unit_exponent)
    else:
        discarded = -unit_exponent
        if discarded > len(coefficient) or coefficient[-discarded:] != "0" * discarded:
            raise ValueError("historical amount is not exact at ledger scale")
        retained = coefficient[:-discarded]
        if not retained:
            raise ValueError("historical amount units must be positive")
        magnitude = int(retained)
    if magnitude <= 0 or len(str(magnitude)) > _MAX_UNIT_DIGITS:
        raise ValueError("historical amount exceeds the ledger unit bound")
    return -magnitude if match.group("sign") else magnitude


def exact_units(amount: str, *, ledger_scale: int) -> int:
    units = _signed_exact_units(amount, ledger_scale=ledger_scale)
    if units <= 0:
        raise ValueError("historical amount must be unsigned and positive")
    return units


def normalize_legacy_signed_amount(
    amount: str, *, ledger_scale: int
) -> ExactPostingAmount:
    signed = _signed_exact_units(amount, ledger_scale=ledger_scale)
    return ExactPostingAmount(
        side="debit" if signed > 0 else "credit",
        units=abs(signed),
    )


def normalize_explicit_amount(
    amount: str, *, side: str, ledger_scale: int
) -> ExactPostingAmount:
    if type(amount) is not str or _UNSIGNED_HISTORICAL_DECIMAL.fullmatch(amount) is None:
        raise ValueError("explicit debit/credit amount must be unsigned decimal text")
    if side not in {"debit", "credit"}:
        raise ValueError("explicit posting side must be debit or credit")
    return ExactPostingAmount(
        side=side,
        units=exact_units(amount, ledger_scale=ledger_scale),
    )


__all__ = [
    "ExactPostingAmount",
    "HistoricalAssetScale",
    "exact_units",
    "normalize_explicit_amount",
    "normalize_legacy_signed_amount",
]
