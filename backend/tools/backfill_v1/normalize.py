from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping
from uuid import UUID

from .namespaces import deterministic_uuid


_PLAIN_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_HISTORICAL_DECIMAL = re.compile(
    r"^(?P<sign>-?)(?P<whole>0|[1-9][0-9]*)"
    r"(?:\.(?P<fraction>[0-9]+))?"
    r"(?:[eE](?P<exponent>[+-]?[0-9]+))?$"
)


def _exact_units_from_historical_decimal(amount: str, *, scale: int) -> int:
    match = _HISTORICAL_DECIMAL.fullmatch(amount)
    if match is None:
        raise ValueError("backfill amount must be an exact decimal string")

    fraction = match.group("fraction") or ""
    coefficient = (match.group("whole") + fraction).lstrip("0")
    if not coefficient:
        return 0

    raw_exponent = match.group("exponent") or "0"
    exponent_digits = raw_exponent.lstrip("+-").lstrip("0")
    if len(exponent_digits) > 6:
        raise ValueError("backfill amount exponent is outside the supported range")
    normalized_exponent = exponent_digits or "0"
    exponent = int(
        f"-{normalized_exponent}"
        if raw_exponent.startswith("-")
        else normalized_exponent
    )
    unit_exponent = exponent - len(fraction) + scale

    if unit_exponent >= 0:
        if len(coefficient) + unit_exponent > 48:
            raise ValueError("backfill amount exceeds the V2 unit bound")
        magnitude = int(coefficient) * (10**unit_exponent)
    else:
        discarded_digits = -unit_exponent
        if (
            discarded_digits > len(coefficient)
            or coefficient[-discarded_digits:] != "0" * discarded_digits
        ):
            raise ValueError(
                "backfill amount is not exactly representable at ledger scale"
            )
        retained = coefficient[:-discarded_digits]
        if len(retained) > 48:
            raise ValueError("backfill amount exceeds the V2 unit bound")
        magnitude = 0 if not retained else int(retained)

    if len(str(magnitude)) > 48:
        raise ValueError("backfill amount exceeds the V2 unit bound")
    return -magnitude if match.group("sign") else magnitude


@dataclass(frozen=True, slots=True)
class NormalizedPosting:
    posting_id: UUID
    account_id: UUID
    asset_code: str
    side: str
    units: int


@dataclass(frozen=True, slots=True)
class NormalizedCategoryVersion:
    category_version_id: UUID
    name: str
    parent_category_id: UUID | None
    status: str


def decimal_to_units(
    amount: str,
    *,
    asset_code: str,
    ledger_scale: int,
    backfill_mode: bool,
) -> int:
    if type(amount) is not str:
        raise TypeError("backfill amount must be an exact decimal string")
    accepted_pattern = _HISTORICAL_DECIMAL if backfill_mode else _PLAIN_DECIMAL
    if not accepted_pattern.fullmatch(amount):
        raise ValueError("backfill amount must be a plain decimal string")
    if type(asset_code) is not str or not asset_code:
        raise ValueError("asset code must be nonblank")
    if type(ledger_scale) is not int or not 0 <= ledger_scale <= 30:
        raise ValueError("ledger scale is outside its allowed range")
    if type(backfill_mode) is not bool:
        raise TypeError("backfill_mode must be a boolean")
    if asset_code == "USDT" and ledger_scale == 8 and not backfill_mode:
        raise ValueError("USDT eight-decimal values require backfill mode")
    return _exact_units_from_historical_decimal(amount, scale=ledger_scale)


def normalize_legacy_signed_posting(
    *,
    source_book_id: str,
    source_transaction_id: str,
    source_posting_id: str,
    source_account_id: str,
    asset_code: str,
    amount: str,
    ledger_scale: int,
    backfill_mode: bool,
) -> NormalizedPosting:
    signed_units = decimal_to_units(
        amount,
        asset_code=asset_code,
        ledger_scale=ledger_scale,
        backfill_mode=backfill_mode,
    )
    if signed_units == 0:
        raise ValueError("legacy posting quantity must be nonzero")
    return NormalizedPosting(
        posting_id=deterministic_uuid(
            "posting",
            source_book_id,
            source_transaction_id,
            source_posting_id,
        ),
        account_id=deterministic_uuid(
            "account",
            source_book_id,
            source_account_id,
        ),
        asset_code=asset_code,
        side="debit" if signed_units > 0 else "credit",
        units=abs(signed_units),
    )


def normalize_category_version(
    source: Mapping[str, object],
) -> NormalizedCategoryVersion:
    try:
        category_version_id = UUID(str(source["category_version_id"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError("category version identity must be a UUID") from None
    raw_parent = source.get("parent_category_id")
    try:
        parent_id = None if raw_parent is None else UUID(str(raw_parent))
    except (TypeError, ValueError):
        raise ValueError("category parent identity must be a UUID") from None
    name = source.get("name")
    status = source.get("status")
    if type(name) is not str or not name:
        raise ValueError("category version snapshot name must be nonblank")
    if status not in {"active", "archived"}:
        raise ValueError("category version snapshot status is invalid")
    return NormalizedCategoryVersion(
        category_version_id=category_version_id,
        name=name,
        parent_category_id=parent_id,
        status=status,
    )


__all__ = [
    "NormalizedCategoryVersion",
    "NormalizedPosting",
    "decimal_to_units",
    "normalize_category_version",
    "normalize_legacy_signed_posting",
]
