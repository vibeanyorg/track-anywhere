from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from track_anywhere.domain.money import (
    AmountScaleExceeded,
    AssetPolicy,
    InvalidScale,
    ScaledUnits,
    USDT_POLICY,
)


def test_usdt_online_rejects_seven_decimals() -> None:
    with pytest.raises(AmountScaleExceeded):
        USDT_POLICY.parse_online("0.1234567")


def test_usdt_online_rejects_a_seventh_decimal_even_when_it_is_zero() -> None:
    with pytest.raises(AmountScaleExceeded):
        USDT_POLICY.parse_online("0.1234560")


def test_usdt_online_scales_six_decimal_input_to_eight_digit_ledger_units() -> None:
    assert USDT_POLICY.parse_online("0.123456") == ScaledUnits(
        units=12_345_600,
        scale=8,
    )


def test_usdt_policy_has_the_approved_scales() -> None:
    assert USDT_POLICY.input_scale == 6
    assert USDT_POLICY.ledger_scale == 8


@pytest.mark.parametrize(
    ("input_scale", "ledger_scale"),
    [
        (-1, 8),
        (9, 8),
        (0, -1),
        (0, 31),
        (True, 8),
        (6, False),
        (6.0, 8),
        (6, 8.0),
    ],
)
def test_asset_policy_rejects_invalid_scale_configuration(
    input_scale: object,
    ledger_scale: object,
) -> None:
    with pytest.raises(InvalidScale):
        AssetPolicy(
            input_scale=input_scale,  # type: ignore[arg-type]
            ledger_scale=ledger_scale,  # type: ignore[arg-type]
        )


def test_asset_policy_is_frozen_and_uses_slots() -> None:
    policy = AssetPolicy(input_scale=2, ledger_scale=4)

    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.input_scale = 3  # type: ignore[misc]
