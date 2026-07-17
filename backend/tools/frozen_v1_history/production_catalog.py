from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from track_anywhere.application.imports.contracts import (
    FrozenFinancialHistoryPlan,
    PlannedAccount,
    PlannedAsset,
    canonical_plan_bytes,
    parse_canonical_plan_bytes,
    plan_sha256,
)
from track_anywhere.infrastructure.db.engine import (
    create_v2_engine,
    require_postgres_17,
)
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = (
    ROOT / "backend/tests/v2/imports/fixtures/frozen_production_catalog_baseline.json"
)
TARGET_BOOK_ID: Final = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
PRODUCTION_FIXTURE_SHA256: Final = (
    "501e5f1886e88a5d86f52e52a8e8e0c2c7cfdfc80f72f64d219828889bbe3cd2"
)
PRODUCTION_IDENTITY_SHA256: Final = (
    "3b7556099f961ffdd65869fd2cd41af97aa0360406586734fab0cd71bce2dc02"
)
PRODUCTION_PLAN_SHA256: Final = (
    "c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8"
)
PRODUCTION_ASSET_CODES: Final = (
    "CNY",
    "DOGE",
    "ETH",
    "EUR",
    "EUR24",
    "GBP",
    "HKD",
    "MATIC",
    "NOT",
    "ORDI",
    "TON",
    "USD",
    "USD24",
    "USDC",
    "USDT",
    "WLD",
)
_HEX_SHA256: Final = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)


class ProductionCatalogError(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CatalogFixture:
    target_book_id: UUID
    asset_codes: tuple[str, ...]
    account_ids: tuple[UUID, ...]
    identity_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedCatalog:
    target_book_id: UUID
    assets: tuple[PlannedAsset, ...]
    accounts: tuple[PlannedAccount, ...]
    fixture_identity_sha256: str
    plan_sha256: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def catalog_identity_sha256(
    target_book_id: UUID,
    *,
    asset_codes: Sequence[str],
    account_ids: Sequence[UUID],
) -> str:
    identity = {
        "account_ids": [str(account_id) for account_id in account_ids],
        "asset_codes": list(asset_codes),
        "target_book_id": str(target_book_id),
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def load_production_catalog_fixture() -> CatalogFixture:
    try:
        raw = FIXTURE_PATH.read_bytes()
        if not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), PRODUCTION_FIXTURE_SHA256
        ):
            raise ValueError
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
        if type(parsed) is not dict or set(parsed) != {
            "account_ids",
            "asset_codes",
            "contract_version",
            "target_book_id",
        }:
            raise ValueError
        if parsed["contract_version"] != 1 or parsed["target_book_id"] != str(
            TARGET_BOOK_ID
        ):
            raise ValueError
        raw_assets = parsed["asset_codes"]
        raw_accounts = parsed["account_ids"]
        if (
            type(raw_assets) is not list
            or tuple(raw_assets) != PRODUCTION_ASSET_CODES
            or type(raw_accounts) is not list
            or len(raw_accounts) != 64
            or raw_accounts != sorted(raw_accounts)
            or len(set(raw_accounts)) != 64
            or any(type(value) is not str for value in raw_accounts)
        ):
            raise ValueError
        account_ids = tuple(UUID(value) for value in raw_accounts)
        if any(str(value) != raw for value, raw in zip(account_ids, raw_accounts)):
            raise ValueError
        identity_hash = catalog_identity_sha256(
            TARGET_BOOK_ID,
            asset_codes=PRODUCTION_ASSET_CODES,
            account_ids=account_ids,
        )
        if not hmac.compare_digest(identity_hash, PRODUCTION_IDENTITY_SHA256):
            raise ValueError
        return CatalogFixture(
            target_book_id=TARGET_BOOK_ID,
            asset_codes=PRODUCTION_ASSET_CODES,
            account_ids=account_ids,
            identity_sha256=identity_hash,
        )
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise ProductionCatalogError("fixture_contract_mismatch") from None


def prepare_catalog(
    plan: FrozenFinancialHistoryPlan,
    *,
    fixture: CatalogFixture,
    expected_plan_sha256: str,
) -> PreparedCatalog:
    try:
        if (
            type(plan) is not FrozenFinancialHistoryPlan
            or _HEX_SHA256.fullmatch(expected_plan_sha256) is None
            or fixture.target_book_id != TARGET_BOOK_ID
            or len(fixture.asset_codes) != 16
            or tuple(sorted(set(fixture.asset_codes))) != fixture.asset_codes
            or len(fixture.account_ids) != 64
            or tuple(sorted(set(fixture.account_ids), key=str)) != fixture.account_ids
            or not hmac.compare_digest(
                fixture.identity_sha256,
                catalog_identity_sha256(
                    fixture.target_book_id,
                    asset_codes=fixture.asset_codes,
                    account_ids=fixture.account_ids,
                ),
            )
        ):
            raise ValueError
        validated = parse_canonical_plan_bytes(canonical_plan_bytes(plan))
        actual_hash = plan_sha256(validated)
        if validated.target_book_id != TARGET_BOOK_ID or not hmac.compare_digest(
            actual_hash, expected_plan_sha256
        ):
            raise ValueError
        assets_by_code = {asset.asset_code: asset for asset in validated.assets}
        accounts_by_id = {account.account_id: account for account in validated.accounts}
        assets = tuple(assets_by_code[code] for code in fixture.asset_codes)
        accounts = tuple(
            accounts_by_id[account_id] for account_id in fixture.account_ids
        )
        if (
            len(assets) != 16
            or len(accounts) != 64
            or any(
                account.asset_code not in fixture.asset_codes for account in accounts
            )
        ):
            raise ValueError
        return PreparedCatalog(
            target_book_id=TARGET_BOOK_ID,
            assets=assets,
            accounts=accounts,
            fixture_identity_sha256=fixture.identity_sha256,
            plan_sha256=actual_hash,
        )
    except (KeyError, TypeError, ValueError):
        raise ProductionCatalogError("plan_contract_mismatch") from None


def prepare_production_catalog(
    plan: FrozenFinancialHistoryPlan,
    *,
    expected_plan_sha256: str,
) -> PreparedCatalog:
    if not hmac.compare_digest(expected_plan_sha256, PRODUCTION_PLAN_SHA256):
        raise ProductionCatalogError("plan_contract_mismatch")
    return prepare_catalog(
        plan,
        fixture=load_production_catalog_fixture(),
        expected_plan_sha256=expected_plan_sha256,
    )


def _asset_row(asset: PlannedAsset) -> dict[str, object]:
    return {
        "asset_code": asset.asset_code,
        "kind": asset.kind,
        "ledger_scale": asset.ledger_scale,
        "input_scale": asset.input_scale,
        "display_scale": asset.display_scale,
        "current_name": asset.current_name,
        "status": asset.status,
    }


def _account_row(book_id: UUID, account: PlannedAccount) -> dict[str, object]:
    return {
        "book_id": book_id,
        "account_id": account.account_id,
        "asset_code": account.asset_code,
        "account_type": account.account_type,
        "account_subtype": account.account_subtype,
        "system_role": account.system_role,
        "current_name": account.current_name,
        "status": account.status,
    }


def _catalog_sha256(prepared: PreparedCatalog) -> str:
    payload = {
        "accounts": [
            {
                key: str(value) if isinstance(value, UUID) else value
                for key, value in _account_row(prepared.target_book_id, account).items()
            }
            for account in prepared.accounts
        ],
        "assets": [_asset_row(asset) for asset in prepared.assets],
        "target_book_id": str(prepared.target_book_id),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def production_catalog_summary(prepared: PreparedCatalog) -> dict[str, int | str]:
    return {
        "accounts": len(prepared.accounts),
        "assets": len(prepared.assets),
        "catalog_sha256": _catalog_sha256(prepared),
        "fixture_identity_sha256": prepared.fixture_identity_sha256,
        "plan_sha256": prepared.plan_sha256,
        "status": "PASS",
    }


def write_production_catalog(
    database_url: str,
    prepared: PreparedCatalog,
    *,
    engine_factory: Callable[[str], Engine] = create_v2_engine,
) -> None:
    engine: Engine | None = None
    try:
        engine = engine_factory(database_url)
        with engine.begin() as connection:
            require_postgres_17(connection)
            connection.execute(
                AssetRecord.__table__.insert(),
                [_asset_row(asset) for asset in prepared.assets],
            )
            connection.execute(
                BookRecord.__table__.insert(),
                {
                    "book_id": prepared.target_book_id,
                    "current_name": "Frozen V1 rehearsal target",
                    "base_asset_code": None,
                    "write_state": "active",
                },
            )
            connection.execute(
                text(
                    "insert into book_event_heads "
                    "(book_id, last_position, last_hash) "
                    "values (:book_id, 0, :zero_hash)"
                ),
                {"book_id": prepared.target_book_id, "zero_hash": bytes(32)},
            )
            connection.execute(
                AccountRecord.__table__.insert(),
                [
                    _account_row(prepared.target_book_id, account)
                    for account in prepared.accounts
                ],
            )
    except Exception:
        raise ProductionCatalogError("catalog_seed_failed") from None
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                raise ProductionCatalogError("catalog_seed_failed") from None


def seed_production_catalog(
    database_url: str,
    plan: FrozenFinancialHistoryPlan,
    *,
    expected_plan_sha256: str,
    engine_factory: Callable[[str], Engine] = create_v2_engine,
) -> PreparedCatalog:
    prepared = prepare_production_catalog(
        plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    write_production_catalog(
        database_url,
        prepared,
        engine_factory=engine_factory,
    )
    return prepared


__all__ = [
    "CatalogFixture",
    "PreparedCatalog",
    "PRODUCTION_IDENTITY_SHA256",
    "PRODUCTION_PLAN_SHA256",
    "ProductionCatalogError",
    "catalog_identity_sha256",
    "load_production_catalog_fixture",
    "prepare_catalog",
    "prepare_production_catalog",
    "production_catalog_summary",
    "seed_production_catalog",
    "write_production_catalog",
]
