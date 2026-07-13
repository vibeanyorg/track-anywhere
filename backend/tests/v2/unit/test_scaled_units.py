from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from track_anywhere.domain.money import (
    AmountOutOfRange,
    AmountScaleExceeded,
    InvalidAmountFormat,
    InvalidScale,
    ScaledUnits,
)


@pytest.mark.parametrize(
    ("raw", "scale", "units"),
    [
        ("12.34", 2, 1_234),
        ("12", 0, 12),
        ("0.00000001", 8, 1),
        ("9.126095", 6, 9_126_095),
        ("1.000000000000000000", 18, 10**18),
    ],
)
def test_parse_exact_units(raw: str, scale: int, units: int) -> None:
    amount = ScaledUnits.parse(raw, scale=scale, max_input_scale=scale)

    assert amount == ScaledUnits(units=units, scale=scale)


@pytest.mark.parametrize(
    "raw",
    [
        12.34,
        12,
        True,
        None,
        "1e2",
        "1E2",
        "+1",
        "-1",
        "-0",
        "NaN",
        "Infinity",
        "inf",
        " 1",
        "1 ",
        "1 0",
        "1\t0",
        "1\n",
        ".1",
        "1.",
        "",
    ],
)
def test_parse_rejects_non_plain_unsigned_decimal_input(raw: object) -> None:
    with pytest.raises(InvalidAmountFormat):
        ScaledUnits.parse(raw, scale=2, max_input_scale=2)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["0", "0.0", "0.00000000"])
def test_parse_rejects_zero(raw: str) -> None:
    with pytest.raises(AmountOutOfRange):
        ScaledUnits.parse(raw, scale=8, max_input_scale=8)


@pytest.mark.parametrize("raw", ["1.234", "1.230", "0.001"])
def test_parse_rejects_any_fraction_beyond_the_input_scale(raw: str) -> None:
    with pytest.raises(AmountScaleExceeded):
        ScaledUnits.parse(raw, scale=2, max_input_scale=2)


@pytest.mark.parametrize("raw", ["9" * 39, "9" * 5_000])
def test_parse_rejects_units_beyond_the_38_digit_boundary(raw: str) -> None:
    with pytest.raises(AmountOutOfRange):
        ScaledUnits.parse(raw, scale=0, max_input_scale=0)


def test_parse_accepts_and_decodes_the_38_digit_boundary_exactly() -> None:
    raw = "9" * 38

    amount = ScaledUnits.parse(raw, scale=0, max_input_scale=0)

    assert amount.units == int(raw)
    assert amount.decode() == raw


@pytest.mark.parametrize(
    ("units", "scale", "decoded"),
    [
        (12, 0, "12"),
        (1, 8, "0.00000001"),
        (1_234, 2, "12.34"),
        (1_234_000, 4, "123.4"),
        (1_200, 2, "12"),
        (10**18, 18, "1"),
    ],
)
def test_decode_returns_a_canonical_non_exponent_decimal(
    units: int,
    scale: int,
    decoded: str,
) -> None:
    assert ScaledUnits(units=units, scale=scale).decode() == decoded


@pytest.mark.parametrize(
    ("raw", "scale"),
    [
        ("12.34", 2),
        ("12", 0),
        ("0.00000001", 8),
        ("9.126095", 6),
        ("1.000000000000000000", 18),
    ],
)
def test_decode_round_trips_the_exact_units(raw: str, scale: int) -> None:
    amount = ScaledUnits.parse(raw, scale=scale, max_input_scale=scale)

    reparsed = ScaledUnits.parse(
        amount.decode(),
        scale=amount.scale,
        max_input_scale=amount.scale,
    )

    assert reparsed == amount


@pytest.mark.parametrize(
    ("scale", "max_input_scale"),
    [
        (-1, 0),
        (31, 0),
        (2, -1),
        (2, 3),
        (True, 0),
        (2, False),
        (2.0, 2),
        (2, 2.0),
    ],
)
def test_parse_rejects_invalid_scale_configuration(
    scale: object,
    max_input_scale: object,
) -> None:
    with pytest.raises(InvalidScale):
        ScaledUnits.parse(
            "1",
            scale=scale,  # type: ignore[arg-type]
            max_input_scale=max_input_scale,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("units", [0, -1, 10**38, True, 1.5])
def test_direct_construction_enforces_positive_38_digit_integer_units(units: object) -> None:
    with pytest.raises(AmountOutOfRange):
        ScaledUnits(units=units, scale=2)  # type: ignore[arg-type]


def test_direct_construction_rejects_an_arbitrarily_large_integer_cleanly() -> None:
    with pytest.raises(AmountOutOfRange):
        ScaledUnits(units=10**5_000, scale=2)


@pytest.mark.parametrize("scale", [-1, 31, True, 1.5])
def test_direct_construction_rejects_invalid_scale(scale: object) -> None:
    with pytest.raises(InvalidScale):
        ScaledUnits(units=1, scale=scale)  # type: ignore[arg-type]


def test_scaled_units_is_frozen_and_uses_slots() -> None:
    amount = ScaledUnits(units=1, scale=8)

    assert not hasattr(amount, "__dict__")
    with pytest.raises(FrozenInstanceError):
        amount.units = 2  # type: ignore[misc]
