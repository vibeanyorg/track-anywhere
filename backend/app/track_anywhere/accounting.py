from __future__ import annotations

from decimal import Decimal
from typing import Literal

from .errors import ValidationError

PostingSide = Literal["debit", "credit"]
PostingAmountSemantics = Literal["legacy_signed", "debit_credit"]
MISSING_STORAGE_AMOUNT_SEMANTICS = "legacy_signed"
STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING = object()

DEBIT_NORMAL_ACCOUNT_TYPES = frozenset({"asset", "expense", "fund", "system"})
CREDIT_NORMAL_ACCOUNT_TYPES = frozenset({"liability", "income", "equity"})


def validate_posting_side(side: str | None) -> PostingSide:
    if side not in {"debit", "credit"}:
        raise ValidationError("posting side must be debit or credit")
    return side  # type: ignore[return-value]


def validate_posting_amount_semantics(value: str) -> PostingAmountSemantics:
    if value not in {"legacy_signed", "debit_credit"}:
        raise ValidationError("posting amount semantics must be legacy_signed or debit_credit")
    return value  # type: ignore[return-value]


def storage_posting_amount_semantics(
    value: str | None | object = STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING,
) -> PostingAmountSemantics:
    """Interpret missing persisted semantics as pre-cutover legacy signed rows only.

    New in-memory postings default to debit_credit. This storage adapter is the
    narrow compatibility bridge for rows created before amount_semantics existed.
    A present-but-NULL column is dirty current storage, not a missing legacy
    column, and must not silently become legacy_signed.
    """

    if value is STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING:
        return MISSING_STORAGE_AMOUNT_SEMANTICS
    if value is None:
        raise ValidationError("posting amount semantics must not be null")
    if not isinstance(value, str):
        raise ValidationError("posting amount semantics must be legacy_signed or debit_credit")
    return validate_posting_amount_semantics(value)


def storage_posting_amount_semantics_or_dirty(
    value: str | None | object = STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING,
) -> str:
    """Load persisted semantics while preserving dirty values for audit."""

    if value is STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING or value is None or not isinstance(value, str):
        return storage_posting_amount_semantics(value)
    if value in {"legacy_signed", "debit_credit"}:
        return validate_posting_amount_semantics(value)
    return value


def validate_positive_posting_amount(amount: Decimal) -> None:
    if amount <= Decimal("0"):
        raise ValidationError("posting amount must be positive")


def normal_balance_side(account_type: str) -> PostingSide:
    if account_type in DEBIT_NORMAL_ACCOUNT_TYPES:
        return "debit"
    if account_type in CREDIT_NORMAL_ACCOUNT_TYPES:
        return "credit"
    raise ValidationError(f"account type is not supported for debit/credit posting: {account_type}")


def natural_balance_delta(account_type: str, side: PostingSide, amount: Decimal) -> Decimal:
    validate_positive_posting_amount(amount)
    side = validate_posting_side(side)
    normal_side = normal_balance_side(account_type)
    return amount if side == normal_side else -amount


def natural_balance_from_totals(account_type: str, *, debits: Decimal, credits: Decimal) -> Decimal:
    normal_side = normal_balance_side(account_type)
    if normal_side == "debit":
        return debits - credits
    return credits - debits


def legacy_signed_amount_to_debit_credit(account_type: str, signed_amount: Decimal) -> tuple[PostingSide, Decimal]:
    """Convert the old raw balancing sign to a debit/credit side.

    This is intentionally not account-type natural-delta conversion. A negative
    legacy amount becomes a credit side for every account type, and a positive
    legacy amount becomes a debit side for every account type. Credit-normal
    accounts whose economic meaning is ambiguous, especially credit cards, must
    be handled by the posting-semantics audit and manual review path.
    """
    normal_balance_side(account_type)
    if signed_amount == Decimal("0"):
        raise ValidationError("legacy posting amount must not be zero")
    if signed_amount > Decimal("0"):
        return "debit", signed_amount
    return "credit", -signed_amount


def posting_balance_delta(
    account_type: str,
    *,
    side: PostingSide | None,
    amount: Decimal,
    amount_semantics: PostingAmountSemantics,
) -> Decimal:
    amount_semantics = validate_posting_amount_semantics(amount_semantics)
    if amount_semantics == "legacy_signed":
        if amount == Decimal("0"):
            raise ValidationError("legacy posting amount must not be zero")
        return amount
    validate_positive_posting_amount(amount)
    if side is None:
        raise ValidationError("debit/credit posting requires side")
    return natural_balance_delta(account_type, side, amount)


def validate_posting_semantic_shape(
    *,
    side: PostingSide | str | None,
    amount: Decimal,
    amount_semantics: PostingAmountSemantics | str,
) -> PostingAmountSemantics:
    amount_semantics = validate_posting_amount_semantics(amount_semantics)
    if amount_semantics == "legacy_signed":
        if amount == Decimal("0"):
            raise ValidationError("legacy posting amount must not be zero")
        return amount_semantics
    validate_positive_posting_amount(amount)
    if side is None:
        raise ValidationError("debit/credit posting requires side")
    validate_posting_side(side)
    return amount_semantics


def debit_credit_balanced(totals: dict[str, dict[PostingSide, Decimal]]) -> dict[str, Decimal]:
    unbalanced: dict[str, Decimal] = {}
    for currency, side_totals in totals.items():
        delta = side_totals.get("debit", Decimal("0")) - side_totals.get("credit", Decimal("0"))
        if delta != Decimal("0"):
            unbalanced[currency] = delta
    return unbalanced
