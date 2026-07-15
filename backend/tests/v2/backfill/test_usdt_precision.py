from __future__ import annotations

import pytest

from backend.tools.backfill_v1.normalize import decimal_to_units


def test_usdt_eight_decimal_quantity_is_accepted_only_in_backfill_mode() -> None:
    assert (
        decimal_to_units(
            "1.12345678",
            asset_code="USDT",
            ledger_scale=8,
            backfill_mode=True,
        )
        == 112_345_678
    )
    with pytest.raises(ValueError, match="backfill mode"):
        decimal_to_units(
            "1.12345678",
            asset_code="USDT",
            ledger_scale=8,
            backfill_mode=False,
        )


@pytest.mark.parametrize("amount", ["0.000000001", "1.123456789"])
def test_usdt_quantity_cannot_be_rounded(amount: str) -> None:
    with pytest.raises(ValueError, match="exactly representable"):
        decimal_to_units(
            amount,
            asset_code="USDT",
            ledger_scale=8,
            backfill_mode=True,
        )


def test_float_never_enters_backfill_amount_normalization() -> None:
    with pytest.raises(TypeError, match="decimal string"):
        decimal_to_units(
            1.25,  # type: ignore[arg-type]
            asset_code="USDT",
            ledger_scale=8,
            backfill_mode=True,
        )


@pytest.mark.parametrize(
    ("amount", "expected_units"),
    [("1E-8", 10_000_000_000), ("-1E-8", -10_000_000_000)],
)
def test_historical_scientific_decimal_is_exactly_normalized(
    amount: str,
    expected_units: int,
) -> None:
    assert (
        decimal_to_units(
            amount,
            asset_code="ETH",
            ledger_scale=18,
            backfill_mode=True,
        )
        == expected_units
    )


def test_scientific_decimal_is_rejected_outside_backfill_mode() -> None:
    with pytest.raises(ValueError, match="plain decimal"):
        decimal_to_units(
            "1E-8",
            asset_code="ETH",
            ledger_scale=18,
            backfill_mode=False,
        )
