from __future__ import annotations

import pytest

from backend.tools.frozen_v1_history.normalize import (
    HistoricalAssetScale,
    exact_units,
    normalize_explicit_amount,
    normalize_legacy_signed_amount,
)


def test_historical_usdt_uses_ledger_8_but_preserves_input_and_display_6() -> None:
    assert HistoricalAssetScale.for_source(
        asset_code="USDT", source_scale=6, source_display_scale=6
    ) == HistoricalAssetScale(ledger_scale=8, input_scale=6, display_scale=6)
    assert exact_units("0.00000001", ledger_scale=8) == 1


@pytest.mark.parametrize(
    ("amount", "scale", "units"),
    [
        ("12.34", 2, 1234),
        ("12.3400", 2, 1234),
        ("1E-8", 8, 1),
        ("100e-2", 2, 100),
    ],
)
def test_exact_units_accept_only_exactly_representable_decimal_text(
    amount: str, scale: int, units: int
) -> None:
    assert exact_units(amount, ledger_scale=scale) == units


@pytest.mark.parametrize(
    "amount",
    [
        1.25,
        "nan",
        "inf",
        "0",
        "-0.00",
        "0.000000001",
        "100000000000000000000000000000000000000",
    ],
)
def test_exact_units_reject_float_zero_inexact_nonfinite_and_overflow(amount: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        exact_units(amount, ledger_scale=8)  # type: ignore[arg-type]


def test_legacy_signed_and_explicit_debit_credit_are_separate_protocols() -> None:
    assert normalize_legacy_signed_amount("12.34", ledger_scale=2).as_tuple() == (
        "debit",
        1234,
    )
    assert normalize_legacy_signed_amount("-12.34", ledger_scale=2).as_tuple() == (
        "credit",
        1234,
    )
    assert normalize_explicit_amount(
        "12.34", side="credit", ledger_scale=2
    ).as_tuple() == ("credit", 1234)
    with pytest.raises(ValueError, match="unsigned"):
        normalize_explicit_amount("-12.34", side="credit", ledger_scale=2)
    with pytest.raises(ValueError, match="side"):
        normalize_explicit_amount("12.34", side="increase", ledger_scale=2)
