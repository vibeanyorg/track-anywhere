from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .errors import ValidationError


@dataclass(frozen=True)
class AssetDefinition:
    asset_code: str
    kind: str
    scale: int
    name: str
    display_scale: int | None = None
    status: str = "active"
    version: int = 1


DEFAULT_ASSETS: dict[str, AssetDefinition] = {
    "CNY": AssetDefinition("CNY", "fiat", 2, "Chinese yuan"),
    "USD": AssetDefinition("USD", "fiat", 2, "US dollar"),
    "HKD": AssetDefinition("HKD", "fiat", 2, "Hong Kong dollar"),
    "EUR": AssetDefinition("EUR", "fiat", 2, "Euro"),
    "GBP": AssetDefinition("GBP", "fiat", 2, "British pound"),
    "JPY": AssetDefinition("JPY", "fiat", 0, "Japanese yen"),
    "KRW": AssetDefinition("KRW", "fiat", 0, "South Korean won"),
    "VND": AssetDefinition("VND", "fiat", 0, "Vietnamese dong"),
    "BTC": AssetDefinition("BTC", "crypto", 8, "Bitcoin"),
    "ETH": AssetDefinition("ETH", "crypto", 18, "Ether"),
    "USDC": AssetDefinition("USDC", "crypto", 6, "USD Coin"),
    "USDT": AssetDefinition("USDT", "crypto", 6, "Tether USD"),
}
DEFAULT_CUSTOM_ASSET_SCALE = 8


def default_asset_definition(asset_code: str) -> AssetDefinition:
    asset = DEFAULT_ASSETS.get(asset_code)
    if asset is not None:
        return asset
    return AssetDefinition(asset_code, "custom", DEFAULT_CUSTOM_ASSET_SCALE, asset_code)


class AssetCatalog:
    def __init__(self) -> None:
        self.assets: dict[str, AssetDefinition] = dict(DEFAULT_ASSETS)
        self._dirty_asset_codes: set[str] = set()

    def ensure_defaults(self) -> None:
        for asset in DEFAULT_ASSETS.values():
            self.assets.setdefault(asset.asset_code, asset)

    def ensure(self, asset_code: str) -> AssetDefinition:
        asset = self.assets.get(asset_code)
        if asset is None:
            asset = default_asset_definition(asset_code)
            self.assets[asset_code] = asset
            self._dirty_asset_codes.add(asset_code)
        return asset

    def register(self, asset: AssetDefinition) -> None:
        if asset.scale < 0 or asset.scale > 18:
            raise ValidationError("asset scale must be between 0 and 18")
        if asset.display_scale is not None and (asset.display_scale < 0 or asset.display_scale > 18):
            raise ValidationError("asset display scale must be between 0 and 18")
        if asset.status not in {"active", "disabled"}:
            raise ValidationError("asset status must be active or disabled")
        self.assets[asset.asset_code] = asset
        self._dirty_asset_codes.add(asset.asset_code)

    def dirty_assets(self) -> list[AssetDefinition]:
        return [self.assets[asset_code] for asset_code in self._dirty_asset_codes if asset_code in self.assets]

    def mark_clean(self) -> None:
        self._dirty_asset_codes.clear()

    def scale_for(self, asset_code: str) -> int:
        return self.ensure(asset_code).scale

    def validate_amount(self, asset_code: str, value: Decimal, *, field_name: str = "amount") -> None:
        validate_asset_amount(value, asset_code, field_name=field_name, scale_lookup=self.scale_for)


def fractional_digits(value: Decimal) -> int:
    normalized = value.normalize()
    exponent = normalized.as_tuple().exponent
    return 0 if exponent >= 0 else -exponent


def validate_asset_amount(value: Decimal, asset_code: str, *, field_name: str = "amount", scale_lookup=None) -> None:
    if not value.is_finite():
        raise ValidationError(f"{field_name} must be finite")
    scale = scale_lookup(asset_code) if scale_lookup is not None else default_asset_definition(asset_code).scale
    digits = fractional_digits(value)
    if digits > scale:
        raise ValidationError(f"{field_name} for {asset_code} allows at most {scale} fractional digits; got {digits}")
