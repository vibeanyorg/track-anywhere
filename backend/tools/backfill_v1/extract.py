from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, create_engine, text

from .config import BackfillConfig, current_v2_head
from .inventory import InventoryReport, inventory_rows, write_inventory
from .manifest import (
    FrozenSourceManifest,
    TableManifest,
    assert_target_ready,
    build_manifest,
    canonical_json_bytes,
    read_manifest,
    sha256_file,
    verify_frozen_source,
    write_manifest,
)


@dataclass(frozen=True)
class TableSpec:
    table: str
    primary_key: tuple[str, ...]

    @property
    def sql_path(self) -> Path:
        return Path(__file__).with_name("sql") / f"{self.table}.sql"


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec("assets", ("asset_code",)),
    TableSpec("ledger_books", ("book_id",)),
    TableSpec("accounts", ("account_id",)),
    TableSpec("categories", ("category_id",)),
    TableSpec("category_versions", ("category_version_id",)),
    TableSpec("counterparties", ("counterparty_id",)),
    TableSpec("transactions", ("transaction_id",)),
    TableSpec("postings", ("transaction_id", "position", "id")),
    TableSpec("transaction_lines", ("transaction_id", "position", "line_id")),
    TableSpec("classification_events", ("classification_event_id",)),
    TableSpec("investment_events", ("event_id",)),
    TableSpec("investment_valuations", ("valuation_id",)),
)
_SPECS_BY_TABLE = {spec.table: spec for spec in TABLE_SPECS}


@dataclass(frozen=True)
class ExtractionResult:
    manifest: FrozenSourceManifest
    inventory: InventoryReport


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite decimals cannot enter a frozen extraction")
    return format(value, "f")


def canonicalize_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats cannot enter a frozen extraction")
        return _decimal_text(Decimal(repr(value)))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetimes cannot enter a frozen extraction")
        return (
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize_value(item) for item in value]
    raise TypeError(f"unsupported source value type: {type(value).__name__}")


def canonicalize_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): canonicalize_value(value)
        for key, value in sorted(row.items(), key=lambda pair: str(pair[0]))
    }


def _row_sort_key(table: str, row: Mapping[str, object]) -> tuple[bytes, ...]:
    spec = _SPECS_BY_TABLE.get(table)
    if spec is None or any(column not in row for column in spec.primary_key):
        return (canonical_json_bytes(canonicalize_row(row)),)
    return tuple(
        canonical_json_bytes(canonicalize_value(row[column]))
        for column in spec.primary_key
    )


def _write_ndjson(
    *, table: str, rows: Sequence[Mapping[str, object]], path: Path
) -> tuple[int, str]:
    canonical_rows = [canonicalize_row(row) for row in rows]
    canonical_rows.sort(key=lambda row: _row_sort_key(table, row))
    digest = hashlib.sha256()
    with path.open("xb") as stream:
        for row in canonical_rows:
            line = canonical_json_bytes(row) + b"\n"
            digest.update(line)
            stream.write(line)
    return len(canonical_rows), digest.hexdigest()


def extract_canonical_rows(
    *,
    rows_by_table: Mapping[str, Sequence[Mapping[str, object]]],
    output_dir: Path,
    dump_sha256: str,
    source_revision: str,
) -> FrozenSourceManifest:
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(
            f"output directory already exists: {output_dir}"
        ) from error
    rows_dir = output_dir / "rows"
    rows_dir.mkdir()

    table_manifests: list[TableManifest] = []
    for table in sorted(rows_by_table):
        if not table.replace("_", "").isalnum():
            raise ValueError(f"unsafe source table name: {table}")
        row_count, ndjson_sha256 = _write_ndjson(
            table=table,
            rows=rows_by_table[table],
            path=rows_dir / f"{table}.ndjson",
        )
        spec = _SPECS_BY_TABLE.get(table)
        table_manifests.append(
            TableManifest(
                table=table,
                row_count=row_count,
                ndjson_sha256=ndjson_sha256,
                primary_key=() if spec is None else spec.primary_key,
            )
        )

    manifest = build_manifest(
        dump_sha256=dump_sha256,
        source_revision=source_revision,
        tables=tuple(table_manifests),
    )
    write_manifest(manifest, output_dir / "manifest.json")
    return manifest


def _read_table(
    engine: Engine, spec: TableSpec, *, batch_size: int, shuffle_seed: int
) -> tuple[str, list[dict[str, object]]]:
    sql = spec.sql_path.read_text(encoding="utf-8")
    rows: list[dict[str, object]] = []
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            read_only = connection.exec_driver_sql(
                "SHOW transaction_read_only"
            ).scalar_one()
            if str(read_only).casefold() not in {"on", "true", "1"}:
                raise RuntimeError("source extraction transaction is not read-only")
            result = connection.execute(text(sql)).mappings()
            while batch := result.fetchmany(batch_size):
                rows.extend(dict(row) for row in batch)

    seed_bytes = hashlib.sha256(f"{shuffle_seed}:{spec.table}".encode("utf-8")).digest()
    random.Random(int.from_bytes(seed_bytes[:8], "big")).shuffle(rows)
    return spec.table, rows


def _read_source_revision(engine: Engine) -> str:
    sql = (Path(__file__).with_name("sql") / "source_revision.sql").read_text(
        encoding="utf-8"
    )
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            read_only = connection.exec_driver_sql(
                "SHOW transaction_read_only"
            ).scalar_one()
            if str(read_only).casefold() not in {"on", "true", "1"}:
                raise RuntimeError("source revision transaction is not read-only")
            revisions = tuple(connection.execute(text(sql)).scalars().all())
    if len(revisions) != 1:
        raise ValueError("restored source must contain exactly one Alembic revision")
    return str(revisions[0])


def read_source_rows(
    engine: Engine, *, batch_size: int, workers: int, shuffle_seed: int
) -> dict[str, list[dict[str, object]]]:
    def load(spec: TableSpec) -> tuple[str, list[dict[str, object]]]:
        return _read_table(
            engine,
            spec,
            batch_size=batch_size,
            shuffle_seed=shuffle_seed,
        )

    with ThreadPoolExecutor(max_workers=min(workers, len(TABLE_SPECS))) as executor:
        extracted = executor.map(load, TABLE_SPECS)
        return dict(extracted)


def load_extracted_rows(
    extraction_dir: Path,
) -> tuple[FrozenSourceManifest, dict[str, list[dict[str, Any]]]]:
    extraction_dir = Path(extraction_dir)
    manifest = read_manifest(extraction_dir / "manifest.json")
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for table_manifest in manifest.tables:
        path = extraction_dir / "rows" / f"{table_manifest.table}.ndjson"
        if sha256_file(path) != table_manifest.ndjson_sha256:
            raise ValueError(f"extracted table hash mismatch: {table_manifest.table}")
        with path.open(encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
        if len(rows) != table_manifest.row_count:
            raise ValueError(
                f"extracted table row count mismatch: {table_manifest.table}"
            )
        rows_by_table[table_manifest.table] = rows
    return manifest, rows_by_table


def extract_database(config: BackfillConfig) -> ExtractionResult:
    if config.output_dir.exists():
        raise FileExistsError(f"output directory already exists: {config.output_dir}")
    if config.frozen_manifest_path is None:
        raise ValueError("a frozen source manifest is required for database extraction")

    assert_target_ready(config.target_url, expected_revision=current_v2_head())
    source_engine = create_engine(config.source_url, pool_pre_ping=True)
    try:
        actual_revision = _read_source_revision(source_engine)
        if actual_revision != config.source_revision:
            raise ValueError(
                "restored source revision does not match configured source revision"
            )
        dump_sha256 = sha256_file(config.dump_path)
        frozen_manifest = read_manifest(config.frozen_manifest_path)
        verify_frozen_source(
            dump_path=config.dump_path,
            manifest=frozen_manifest,
            actual_source_revision=actual_revision,
        )
        rows_by_table = read_source_rows(
            source_engine,
            batch_size=config.batch_size,
            workers=config.workers,
            shuffle_seed=config.shuffle_seed,
        )
    finally:
        source_engine.dispose()

    manifest = extract_canonical_rows(
        rows_by_table=rows_by_table,
        output_dir=config.output_dir,
        dump_sha256=dump_sha256,
        source_revision=actual_revision,
    )
    report = inventory_rows(rows_by_table)
    write_inventory(report, config.output_dir / "inventory.json")
    (config.output_dir / "extraction-run.json").write_bytes(
        canonical_json_bytes(
            {
                "batch_size": config.batch_size,
                "shuffle_seed": config.shuffle_seed,
                "workers": config.workers,
            }
        )
        + b"\n"
    )
    return ExtractionResult(manifest=manifest, inventory=report)


__all__ = [
    "ExtractionResult",
    "TABLE_SPECS",
    "canonicalize_row",
    "canonicalize_value",
    "extract_canonical_rows",
    "extract_database",
    "load_extracted_rows",
    "read_source_rows",
]
