from __future__ import annotations

import re
from dataclasses import dataclass


MAX_UNIT_DIGITS = 38
MAX_UNITS_EXCLUSIVE = 10**MAX_UNIT_DIGITS
MAX_SCALE = 30
AMOUNT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?", flags=re.ASCII)


class MoneyError(ValueError):
    """Base class for exact-money validation errors."""


class InvalidAmountFormat(MoneyError):
    """Raised when an amount is not an unsigned plain-decimal string."""

    def __init__(self, raw: object) -> None:
        super().__init__("amount must be an unsigned plain-decimal string")


class AmountScaleExceeded(MoneyError):
    """Raised when an amount contains more fractional digits than permitted."""

    def __init__(self, raw: str, *, max_input_scale: int) -> None:
        super().__init__(
            f"amount exceeds the permitted input scale of {max_input_scale}"
        )


class AmountOutOfRange(MoneyError):
    """Raised when exact units are not positive or exceed 38 digits."""

    def __init__(self, value: object) -> None:
        super().__init__("amount units must be positive and contain at most 38 digits")


class InvalidScale(MoneyError):
    """Raised when a scale is not a supported integer precision."""


def _validate_scale(scale: object, *, label: str) -> int:
    if type(scale) is not int or not 0 <= scale <= MAX_SCALE:
        raise InvalidScale(f"{label} must be an integer between 0 and {MAX_SCALE}")
    return scale


@dataclass(frozen=True, slots=True)
class ScaledUnits:
    units: int
    scale: int

    def __post_init__(self) -> None:
        _validate_scale(self.scale, label="scale")
        if (
            type(self.units) is not int
            or self.units <= 0
            or self.units >= MAX_UNITS_EXCLUSIVE
        ):
            raise AmountOutOfRange(self.units)

    @classmethod
    def parse(
        cls,
        raw: str,
        *,
        scale: int,
        max_input_scale: int,
    ) -> ScaledUnits:
        checked_scale = _validate_scale(scale, label="scale")
        checked_input_scale = _validate_scale(
            max_input_scale,
            label="max_input_scale",
        )
        if checked_input_scale > checked_scale:
            raise InvalidScale("max_input_scale cannot exceed scale")
        if not isinstance(raw, str) or AMOUNT_PATTERN.fullmatch(raw) is None:
            raise InvalidAmountFormat(raw)

        whole, _, fraction = raw.partition(".")
        if len(fraction) > checked_input_scale:
            raise AmountScaleExceeded(raw, max_input_scale=checked_input_scale)

        digits = whole + fraction.ljust(checked_scale, "0")
        significant_digits = digits.lstrip("0")
        if not significant_digits or len(significant_digits) > MAX_UNIT_DIGITS:
            raise AmountOutOfRange(raw)

        return cls(units=int(significant_digits), scale=checked_scale)

    def decode(self) -> str:
        if self.scale == 0:
            return str(self.units)

        digits = str(self.units).zfill(self.scale + 1)
        whole = digits[: -self.scale]
        fraction = digits[-self.scale :].rstrip("0")
        if not fraction:
            return whole
        return f"{whole}.{fraction}"
