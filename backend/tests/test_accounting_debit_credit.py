from __future__ import annotations

from decimal import Decimal

import pytest

from track_anywhere.accounting import (
    debit_credit_balanced,
    legacy_signed_amount_to_debit_credit,
    natural_balance_delta,
    natural_balance_from_totals,
    posting_balance_delta,
    STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING,
    storage_posting_amount_semantics,
    validate_posting_semantic_shape,
)
from track_anywhere.errors import ValidationError


def test_debit_normal_accounts_increase_on_debit_and_decrease_on_credit():
    assert natural_balance_delta("asset", "debit", Decimal("100")) == Decimal("100")
    assert natural_balance_delta("asset", "credit", Decimal("40")) == Decimal("-40")
    assert natural_balance_delta("expense", "debit", Decimal("12.34")) == Decimal("12.34")


def test_credit_normal_accounts_increase_on_credit_and_decrease_on_debit():
    assert natural_balance_delta("liability", "credit", Decimal("100")) == Decimal("100")
    assert natural_balance_delta("liability", "debit", Decimal("40")) == Decimal("-40")
    assert natural_balance_delta("income", "credit", Decimal("12.34")) == Decimal("12.34")


def test_natural_balance_from_debit_credit_totals_uses_account_type():
    assert natural_balance_from_totals("asset", debits=Decimal("100"), credits=Decimal("40")) == Decimal("60")
    assert natural_balance_from_totals("liability", debits=Decimal("40"), credits=Decimal("100")) == Decimal("60")


def test_legacy_signed_amount_conversion_preserves_old_balancing_side_not_natural_delta():
    assert legacy_signed_amount_to_debit_credit("asset", Decimal("100")) == ("debit", Decimal("100"))
    assert legacy_signed_amount_to_debit_credit("asset", Decimal("-100")) == ("credit", Decimal("100"))
    assert legacy_signed_amount_to_debit_credit("liability", Decimal("100")) == ("debit", Decimal("100"))
    assert legacy_signed_amount_to_debit_credit("liability", Decimal("-100")) == ("credit", Decimal("100"))
    assert natural_balance_delta("liability", "credit", Decimal("100")) != Decimal("-100")


def test_legacy_signed_amount_conversion_rejects_unknown_account_type():
    with pytest.raises(ValidationError):
        legacy_signed_amount_to_debit_credit("unknown", Decimal("100"))


def test_debit_credit_balancing_reports_currency_deltas():
    totals = {
        "USD": {"debit": Decimal("12.34"), "credit": Decimal("12.34")},
        "CNY": {"debit": Decimal("20"), "credit": Decimal("10")},
    }

    assert debit_credit_balanced(totals) == {"CNY": Decimal("10")}


def test_posting_balance_delta_supports_legacy_and_debit_credit_semantics():
    assert posting_balance_delta(
        "liability",
        side="credit",
        amount=Decimal("11.08"),
        amount_semantics="debit_credit",
    ) == Decimal("11.08")
    assert posting_balance_delta(
        "liability",
        side="credit",
        amount=Decimal("-11.08"),
        amount_semantics="legacy_signed",
    ) == Decimal("-11.08")


def test_storage_missing_amount_semantics_is_the_only_implicit_legacy_bridge():
    assert storage_posting_amount_semantics() == "legacy_signed"
    assert storage_posting_amount_semantics(STORAGE_POSTING_AMOUNT_SEMANTICS_MISSING) == "legacy_signed"
    assert storage_posting_amount_semantics("debit_credit") == "debit_credit"

    with pytest.raises(ValidationError):
        storage_posting_amount_semantics(None)
    with pytest.raises(ValidationError):
        storage_posting_amount_semantics("signed_raw")


def test_invalid_side_and_zero_amount_are_rejected():
    with pytest.raises(ValidationError):
        natural_balance_delta("asset", "increase", Decimal("1"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        natural_balance_delta("asset", "debit", Decimal("0"))


def test_posting_semantic_shape_rejects_invalid_debit_credit_storage_rows():
    with pytest.raises(ValidationError):
        validate_posting_semantic_shape(side=None, amount=Decimal("1"), amount_semantics="debit_credit")
    with pytest.raises(ValidationError):
        validate_posting_semantic_shape(side="debit", amount=Decimal("-1"), amount_semantics="debit_credit")
    with pytest.raises(ValidationError):
        validate_posting_semantic_shape(side="increase", amount=Decimal("1"), amount_semantics="debit_credit")
    with pytest.raises(ValidationError):
        validate_posting_semantic_shape(side=None, amount=Decimal("0"), amount_semantics="legacy_signed")
    assert (
        validate_posting_semantic_shape(side="credit", amount=Decimal("1"), amount_semantics="debit_credit")
        == "debit_credit"
    )


def test_system_accounts_use_explicit_side_debit_normal_balance_math():
    assert natural_balance_delta("system", "debit", Decimal("10")) == Decimal("10")
    assert natural_balance_delta("system", "credit", Decimal("10")) == Decimal("-10")
