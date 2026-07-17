from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tools.frozen_v1_history import production_catalog
from track_anywhere.application.imports.contracts import (
    canonical_plan_bytes,
    plan_sha256,
)
from track_anywhere.application.imports.import_frozen_financial_history import (
    FROZEN_IMPORT_CATALOG_IDENTITY_SHA256,
)


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/seed-frozen-production-catalog.py"
TARGET_BOOK_ID = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_frozen_catalog", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _synthetic_fixture(module: ModuleType, plan):
    asset_codes = tuple(asset.asset_code for asset in plan.assets[:16])
    account_ids = tuple(account.account_id for account in plan.accounts[:64])
    return module.CatalogFixture(
        target_book_id=TARGET_BOOK_ID,
        asset_codes=asset_codes,
        account_ids=account_ids,
        identity_sha256=module._identity_sha256(
            TARGET_BOOK_ID,
            asset_codes=asset_codes,
            account_ids=account_ids,
        ),
    )


def test_seed_reader_consumes_one_canonical_plan_to_eof_and_enforces_size_cap() -> None:
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = canonical_plan_bytes(plan)

    class FragmentedStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            return super().read(min(size, 31) if size >= 0 else 31)

    assert module._read_canonical_plan(FragmentedStream(raw)) == raw
    with pytest.raises(module.SeedFailure) as caught:
        module._read_canonical_plan(io.BytesIO(b"x" * (module.MAX_STDIN_BYTES + 1)))
    assert caught.value.code == "stdin_too_large"


def test_runtime_preflight_and_rehearsal_share_exact_catalog_identity() -> None:
    fixture = production_catalog.load_production_catalog_fixture()
    computed = production_catalog.catalog_identity_sha256(
        fixture.target_book_id,
        asset_codes=fixture.asset_codes,
        account_ids=fixture.account_ids,
    )

    assert (
        FROZEN_IMPORT_CATALOG_IDENTITY_SHA256
        == production_catalog.PRODUCTION_IDENTITY_SHA256
        == fixture.identity_sha256
        == computed
    )


def test_seed_preparation_selects_only_fixture_identities_from_validated_plan() -> None:
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    fixture = _synthetic_fixture(module, plan)

    prepared = module._prepare_catalog(
        plan,
        fixture=fixture,
        expected_plan_sha256=plan_sha256(plan),
    )

    assert tuple(asset.asset_code for asset in prepared.assets) == fixture.asset_codes
    assert (
        tuple(account.account_id for account in prepared.accounts)
        == fixture.account_ids
    )
    assert len(prepared.assets) == 16
    assert len(prepared.accounts) == 64
    assert prepared.plan_sha256 == plan_sha256(plan)
    summary = module._safe_summary(prepared)
    assert set(summary) == {
        "accounts",
        "assets",
        "catalog_sha256",
        "fixture_identity_sha256",
        "plan_sha256",
        "status",
    }
    rendered = json.dumps(summary, sort_keys=True)
    assert "fixture-account" not in rendered
    assert "fixture-asset" not in rendered
    assert "balance" not in rendered

    changed_account = prepared.accounts[0].model_copy(
        update={"current_name": "different validated name"}
    )
    changed = replace(
        prepared,
        accounts=(changed_account, *prepared.accounts[1:]),
    )
    assert module._safe_summary(changed)["catalog_sha256"] != summary["catalog_sha256"]


def test_plan_contract_failure_happens_before_database_transaction() -> None:
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = canonical_plan_bytes(plan)
    calls: list[str] = []

    def seed_operation(_database_url: str, _prepared) -> None:
        calls.append("transaction")

    with pytest.raises(module.SeedFailure) as caught:
        module._execute(
            ["--plan-sha256", "0" * 64, "--stdin"],
            stdin=io.BytesIO(raw),
            environ={"TRACK_ANYWHERE_DATABASE_URL": "postgresql+psycopg://opaque"},
            fixture_loader=lambda: _synthetic_fixture(module, plan),
            seed_operation=seed_operation,
        )

    assert caught.value.code == "plan_contract_mismatch"
    assert calls == []


def test_seed_cli_rejects_a_self_consistent_but_unpinned_plan_hash() -> None:
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = canonical_plan_bytes(plan)
    calls: list[str] = []

    with pytest.raises(module.SeedFailure) as caught:
        module._execute(
            ["--plan-sha256", plan_sha256(plan), "--stdin"],
            stdin=io.BytesIO(raw),
            environ={"TRACK_ANYWHERE_DATABASE_URL": "postgresql+psycopg://opaque"},
            fixture_loader=lambda: _synthetic_fixture(module, plan),
            seed_operation=lambda *_args: calls.append("transaction"),
        )

    assert caught.value.code == "plan_contract_mismatch"
    assert calls == []


def test_validated_plan_is_written_once_and_returns_only_safe_aggregate_fields() -> (
    None
):
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    raw = canonical_plan_bytes(plan)
    received = []

    def seed_operation(database_url: str, prepared) -> None:
        assert database_url == "postgresql+psycopg://opaque"
        received.append(prepared)

    summary = module._execute(
        ["--plan-sha256", plan_sha256(plan), "--stdin"],
        stdin=io.BytesIO(raw),
        environ={"TRACK_ANYWHERE_DATABASE_URL": "postgresql+psycopg://opaque"},
        fixture_loader=lambda: _synthetic_fixture(module, plan),
        seed_operation=seed_operation,
        required_plan_sha256=plan_sha256(plan),
    )

    assert len(received) == 1
    assert summary == module._safe_summary(received[0])
    assert summary["status"] == "PASS"


def test_database_failure_rolls_back_the_single_seed_transaction() -> None:
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    prepared = module._prepare_catalog(
        plan,
        fixture=_synthetic_fixture(module, plan),
        expected_plan_sha256=plan_sha256(plan),
    )

    class FakeConnection:
        calls = 0

        class VersionResult:
            @staticmethod
            def scalar_one() -> str:
                return "170010"

        def exec_driver_sql(self, _statement):
            return self.VersionResult()

        def execute(self, _statement, _parameters=None):
            self.calls += 1
            if self.calls == 4:
                raise SQLAlchemyError("synthetic database failure")

    class FakeTransaction:
        rolled_back = False
        committed = False

        def __init__(self) -> None:
            self.connection = FakeConnection()

        def __enter__(self):
            return self.connection

        def __exit__(self, exception_type, _exception, _traceback):
            self.rolled_back = exception_type is not None
            self.committed = exception_type is None
            return False

    class FakeEngine:
        disposed = False

        def __init__(self) -> None:
            self.transaction = FakeTransaction()

        def begin(self):
            return self.transaction

        def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()
    with pytest.raises(module.SeedFailure) as caught:
        module._write_catalog(
            "postgresql+psycopg://opaque",
            prepared,
            engine_factory=lambda _url: engine,
        )

    assert caught.value.code == "catalog_seed_failed"
    assert engine.transaction.rolled_back is True
    assert engine.transaction.committed is False
    assert engine.disposed is True


def test_unexpected_database_adapter_failure_is_stable_and_secret_free() -> None:
    module = _load_module()
    plan = build_valid_fixture_plan(target_book_id=TARGET_BOOK_ID)
    prepared = module._prepare_catalog(
        plan,
        fixture=_synthetic_fixture(module, plan),
        expected_plan_sha256=plan_sha256(plan),
    )

    class AdapterFailure(Exception):
        pass

    def fail_engine(_url: str):
        raise AdapterFailure("sentinel-private-dsn")

    with pytest.raises(module.SeedFailure) as caught:
        module._write_catalog(
            "postgresql+psycopg://sentinel-private-dsn",
            prepared,
            engine_factory=fail_engine,
        )

    assert caught.value.code == "catalog_seed_failed"
    assert "sentinel" not in str(caught.value)


def test_seed_script_has_no_plan_spool_or_ledger_write_boundary() -> None:
    runner_source = SCRIPT.read_text(encoding="utf-8")
    catalog_source = Path(production_catalog.__file__).read_text(encoding="utf-8")
    source = runner_source + catalog_source
    digest = hashlib.sha256(source.encode()).hexdigest()

    assert len(digest) == 64
    for forbidden in (
        "NamedTemporaryFile",
        "TemporaryFile",
        "mkstemp",
        "SpooledTemporaryFile",
        '".plan"',
        "'.plan'",
        "LedgerCommitter",
        "EventRecord",
        "JournalTransactionRecord",
        "BalanceRecord",
    ):
        assert forbidden not in source
    assert "with engine.begin() as connection" in catalog_source
    assert "parse_canonical_plan_bytes" in runner_source
    assert "plan_sha256" in catalog_source
    assert callable(production_catalog.seed_production_catalog)
