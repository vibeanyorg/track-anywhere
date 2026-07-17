#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import os
import re
import sys
from typing import BinaryIO, Final

from sqlalchemy.engine import Engine

from backend.tools.frozen_v1_history.production_catalog import (
    CatalogFixture,
    PreparedCatalog,
    PRODUCTION_PLAN_SHA256,
    ProductionCatalogError,
    catalog_identity_sha256,
    load_production_catalog_fixture,
    prepare_catalog,
    production_catalog_summary,
    write_production_catalog,
)
from track_anywhere.application.imports.contracts import parse_canonical_plan_bytes
from track_anywhere.infrastructure.db.engine import create_v2_engine


MAX_STDIN_BYTES: Final = 8 * 1024 * 1024
DATABASE_URL_ENV: Final = "TRACK_ANYWHERE_DATABASE_URL"
_HEX_SHA256: Final = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)


class SeedFailure(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _read_canonical_plan(stdin: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_STDIN_BYTES:
        try:
            chunk = stdin.read(min(1024 * 1024, MAX_STDIN_BYTES + 1 - total))
        except Exception:
            raise SeedFailure("stdin_read_failed") from None
        if type(chunk) is not bytes:
            raise SeedFailure("stdin_read_failed")
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_STDIN_BYTES:
            raise SeedFailure("stdin_too_large")
    raise SeedFailure("stdin_too_large")


def _translate_catalog_error(error: ProductionCatalogError) -> SeedFailure:
    return SeedFailure(error.code)


def _identity_sha256(*args, **kwargs) -> str:
    return catalog_identity_sha256(*args, **kwargs)


def _load_production_fixture() -> CatalogFixture:
    try:
        return load_production_catalog_fixture()
    except ProductionCatalogError as error:
        raise _translate_catalog_error(error) from None


def _prepare_catalog(*args, **kwargs) -> PreparedCatalog:
    try:
        return prepare_catalog(*args, **kwargs)
    except ProductionCatalogError as error:
        raise _translate_catalog_error(error) from None


def _safe_summary(prepared: PreparedCatalog) -> dict[str, int | str]:
    return production_catalog_summary(prepared)


def _write_catalog(
    database_url: str,
    prepared: PreparedCatalog,
    *,
    engine_factory: Callable[[str], Engine] = create_v2_engine,
) -> None:
    try:
        write_production_catalog(
            database_url,
            prepared,
            engine_factory=engine_factory,
        )
    except ProductionCatalogError as error:
        raise _translate_catalog_error(error) from None


def _parse_arguments(argv: Sequence[str]) -> str:
    if (
        len(argv) != 3
        or argv[0] != "--plan-sha256"
        or _HEX_SHA256.fullmatch(argv[1]) is None
        or argv[2] != "--stdin"
    ):
        raise SeedFailure("invalid_arguments")
    return argv[1]


def _required_database_url(environ: Mapping[str, str]) -> str:
    try:
        value = environ.get(DATABASE_URL_ENV)
    except Exception:
        raise SeedFailure("runtime_configuration_invalid") from None
    if type(value) is not str or not value:
        raise SeedFailure("runtime_configuration_invalid")
    return value


def _execute(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    environ: Mapping[str, str],
    fixture_loader: Callable[[], CatalogFixture] = _load_production_fixture,
    seed_operation: Callable[[str, PreparedCatalog], None] = _write_catalog,
    required_plan_sha256: str = PRODUCTION_PLAN_SHA256,
) -> dict[str, int | str]:
    expected_hash = _parse_arguments(argv)
    if (
        _HEX_SHA256.fullmatch(required_plan_sha256) is None
        or expected_hash != required_plan_sha256
    ):
        raise SeedFailure("plan_contract_mismatch")
    raw = _read_canonical_plan(stdin)
    try:
        plan = parse_canonical_plan_bytes(raw)
    except (TypeError, ValueError):
        raise SeedFailure("invalid_plan") from None
    finally:
        raw = b""
    fixture = fixture_loader()
    prepared = _prepare_catalog(
        plan,
        fixture=fixture,
        expected_plan_sha256=expected_hash,
    )
    database_url = _required_database_url(environ)
    seed_operation(database_url, prepared)
    return _safe_summary(prepared)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        summary = _execute(
            tuple(sys.argv[1:] if argv is None else argv),
            stdin=sys.stdin.buffer,
            environ=os.environ,
        )
    except SeedFailure as exc:
        sys.stderr.write(json.dumps({"error": exc.code}, separators=(",", ":")) + "\n")
        return 1
    except Exception:
        sys.stderr.write('{"error":"catalog_seed_failed"}\n')
        return 1
    sys.stdout.write(json.dumps(summary, separators=(",", ":"), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
