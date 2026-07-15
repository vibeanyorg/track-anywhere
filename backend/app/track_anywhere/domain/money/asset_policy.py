from __future__ import annotations

from dataclasses import dataclass

from .scaled_units import InvalidScale, ScaledUnits, _validate_scale


@dataclass(frozen=True, slots=True)
class AssetPolicy:
    input_scale: int
    ledger_scale: int

    def __post_init__(self) -> None:
        checked_input_scale = _validate_scale(self.input_scale, label="input_scale")
        checked_ledger_scale = _validate_scale(self.ledger_scale, label="ledger_scale")
        if checked_input_scale > checked_ledger_scale:
            raise InvalidScale("input_scale cannot exceed ledger_scale")

    def parse_online(self, raw: str) -> ScaledUnits:
        return ScaledUnits.parse(
            raw,
            scale=self.ledger_scale,
            max_input_scale=self.input_scale,
        )

USDT_POLICY = AssetPolicy(input_scale=6, ledger_scale=8)
