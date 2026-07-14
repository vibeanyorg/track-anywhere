from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Mapping
from uuid import UUID

from .namespaces import deterministic_uuid


_PLAIN_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


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
    if not _PLAIN_DECIMAL.fullmatch(amount):
        raise ValueError("backfill amount must be a plain decimal string")
    if type(asset_code) is not str or not asset_code:
        raise ValueError("asset code must be nonblank")
    if type(ledger_scale) is not int or not 0 <= ledger_scale <= 30:
        raise ValueError("ledger scale is outside its allowed range")
    if type(backfill_mode) is not bool:
        raise TypeError("backfill_mode must be a boolean")
    if asset_code == "USDT" and ledger_scale == 8 and not backfill_mode:
        raise ValueError("USDT eight-decimal values require backfill mode")
    try:
        parsed = Decimal(amount)
    except InvalidOperation:
        raise ValueError("backfill amount is invalid") from None
    if not parsed.is_finite():
        raise ValueError("backfill amount must be finite")
    scaled = parsed * (Decimal(10) ** ledger_scale)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError("backfill amount is not exactly representable at ledger scale")
    units = int(integral)
    if len(str(abs(units))) > 48:
        raise ValueError("backfill amount exceeds the V2 unit bound")
    return units


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
