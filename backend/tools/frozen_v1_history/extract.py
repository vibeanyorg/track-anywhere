from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import random
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from .constants import (
    EXPECTED_DUMP_SHA256,
    EXPECTED_SOURCE_PRIMARY_KEYS,
    EXPECTED_SOURCE_REVISION,
    EXPECTED_SOURCE_TABLE_COUNTS,
)
from .manifest import (
    FrozenSourceManifest,
    TableManifest,
    assert_approved_manifest,
    canonical_json_bytes,
)
from .namespaces import deterministic_uuid


ACCOUNT_UUID_MAP_PROTOCOL = "frozen-v1-account-uuid-map/v1-naked-pairs"
ACCOUNT_UUID_MAP_SHA256 = (
    "5ac49a95183e2f6096f497225be7b616af7f35103ddb1c362175115fd1fc5c4f"
)


@dataclass(frozen=True, slots=True)
class TableSpec:
    table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    book_scoped: bool = True

    @property
    def sql_path(self) -> Path:
        return Path(__file__).with_name("sql") / f"{self.table}.sql"


TABLE_SPECS: tuple[TableSpec, ...] = tuple(
    sorted(
        (
            TableSpec(
                "ledger_books",
                (
                    "book_id",
                    "name",
                    "kind",
                    "base_currency",
                    "timezone",
                    "status",
                    "template_key",
                    "settings",
                    "created_by",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["ledger_books"],
                False,
            ),
            TableSpec(
                "assets",
                (
                    "asset_code",
                    "kind",
                    "scale",
                    "display_scale",
                    "name",
                    "status",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["assets"],
                False,
            ),
            TableSpec(
                "accounts",
                (
                    "account_id",
                    "book_id",
                    "name",
                    "type",
                    "currency",
                    "institution_type",
                    "subtype",
                    "institution",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["accounts"],
            ),
            TableSpec(
                "categories",
                (
                    "category_id",
                    "book_id",
                    "kind",
                    "parent_id",
                    "name",
                    "normalized_name",
                    "level",
                    "path_cache",
                    "icon",
                    "color",
                    "sort_order",
                    "status",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["categories"],
            ),
            TableSpec(
                "category_versions",
                (
                    "category_version_id",
                    "category_id",
                    "book_id",
                    "name",
                    "parent_id",
                    "path",
                    "icon",
                    "color",
                    "valid_from",
                    "valid_to",
                    "change_reason",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["category_versions"],
            ),
            TableSpec(
                "transactions",
                (
                    "transaction_id",
                    "book_id",
                    "memo",
                    "occurred_at",
                    "purpose",
                    "reversed_by",
                    "reverses_transaction_id",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["transactions"],
            ),
            TableSpec(
                "postings",
                (
                    "id",
                    "transaction_id",
                    "book_id",
                    "position",
                    "account_id",
                    "side",
                    "amount_semantics",
                    "amount",
                    "currency",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["postings"],
            ),
            TableSpec(
                "transaction_lines",
                (
                    "line_id",
                    "transaction_id",
                    "position",
                    "line_type",
                    "amount",
                    "currency",
                    "book_id",
                    "category_id",
                    "category_version_id",
                    "category_path_snapshot",
                    "counterparty_id",
                    "project_id",
                    "necessity",
                    "reimbursement_status",
                    "memo",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["transaction_lines"],
            ),
            TableSpec(
                "classification_events",
                (
                    "classification_event_id",
                    "book_id",
                    "event_type",
                    "source_category_id",
                    "target_category_id",
                    "affected_line_count",
                    "before",
                    "after",
                    "rollback",
                    "created_by",
                    "created_at",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["classification_events"],
            ),
            TableSpec(
                "investment_events",
                (
                    "event_id",
                    "book_id",
                    "account_id",
                    "event_type",
                    "amount",
                    "currency",
                    "occurred_at",
                    "memo",
                    "units",
                    "nav",
                    "transaction_id",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["investment_events"],
            ),
            TableSpec(
                "investment_valuations",
                (
                    "valuation_id",
                    "book_id",
                    "account_id",
                    "value",
                    "currency",
                    "observed_at",
                    "source",
                    "memo",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["investment_valuations"],
            ),
            TableSpec(
                "counterparties",
                (
                    "counterparty_id",
                    "book_id",
                    "slug",
                    "name",
                    "kind",
                    "status",
                    "version",
                ),
                EXPECTED_SOURCE_PRIMARY_KEYS["counterparties"],
            ),
        ),
        key=lambda spec: spec.table,
    )
)
_SPECS_BY_TABLE = MappingProxyType({spec.table: spec for spec in TABLE_SPECS})


@dataclass(frozen=True, slots=True)
class FrozenTableRows:
    table: str
    rows: tuple[Mapping[str, object], ...] = field(repr=False)
    ndjson_sha256: str


@dataclass(frozen=True, slots=True)
class FrozenSourceRows:
    manifest: FrozenSourceManifest
    tables: Mapping[str, FrozenTableRows] = field(repr=False)
    attachments_count: int


def canonicalize_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimal cannot enter frozen extraction")
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("float cannot enter exact frozen extraction")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime cannot enter frozen extraction")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("JSON object keys must be strings")
        return {
            key: canonicalize_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize_value(item) for item in value]
    raise TypeError(f"unsupported frozen source type: {type(value).__name__}")


def _canonicalize_row(spec: TableSpec, row: Mapping[str, object]) -> dict[str, object]:
    if any(type(column) is not str for column in row):
        raise TypeError("source row column names must be strings")
    if set(row) != set(spec.columns) or len(row) != len(spec.columns):
        raise ValueError("source row columns do not match audited SQL")
    return {column: canonicalize_value(row[column]) for column in sorted(spec.columns)}


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def canonicalize_table_rows(
    spec: TableSpec, rows: Sequence[Mapping[str, object]]
) -> FrozenTableRows:
    canonical_rows = [_canonicalize_row(spec, row) for row in rows]
    seen: set[tuple[bytes, ...]] = set()
    keyed: list[tuple[tuple[bytes, ...], dict[str, object]]] = []
    for row in canonical_rows:
        key = tuple(canonical_json_bytes(row[column]) for column in spec.primary_key)
        if key in seen:
            raise ValueError("source table contains a duplicate primary key")
        seen.add(key)
        keyed.append((key, row))
    keyed.sort(key=lambda item: item[0])
    ordered_mutable = tuple(row for _, row in keyed)
    digest = hashlib.sha256()
    for row in ordered_mutable:
        digest.update(canonical_json_bytes(row) + b"\n")
    ordered = tuple(_deep_freeze(row) for row in ordered_mutable)
    return FrozenTableRows(
        table=spec.table,
        rows=ordered,
        ndjson_sha256=digest.hexdigest(),
    )


def verify_frozen_table_rows(spec: TableSpec, table: FrozenTableRows) -> str:
    if table.table != spec.table:
        raise ValueError("frozen table identity mismatch")
    digest = hashlib.sha256()
    previous_key: tuple[bytes, ...] | None = None
    for row in table.rows:
        thawed = _thaw_json(row)
        if type(thawed) is not dict or set(thawed) != set(spec.columns):
            raise ValueError("frozen table row shape mismatch")
        key = tuple(canonical_json_bytes(thawed[column]) for column in spec.primary_key)
        if previous_key is not None and key <= previous_key:
            raise ValueError("frozen table primary key order is invalid")
        previous_key = key
        digest.update(canonical_json_bytes(thawed) + b"\n")
    calculated = digest.hexdigest()
    if calculated != table.ndjson_sha256:
        raise ValueError("frozen table digest mismatch")
    return calculated


def account_uuid_map_digest(accounts: Sequence[Mapping[str, object]]) -> tuple[int, str]:
    pairs: list[list[str]] = []
    for row in accounts:
        source_book = row.get("book_id")
        source_account = row.get("account_id")
        if (
            type(source_book) is not str
            or not source_book.strip()
            or type(source_account) is not str
            or not source_account.strip()
        ):
            raise ValueError("account UUID aggregate source identity is invalid")
        pairs.append(
            [
                source_account,
                str(deterministic_uuid("account", source_book, source_account)),
            ]
        )
    pairs.sort(key=lambda pair: pair[0].encode("utf-8"))
    return len(pairs), hashlib.sha256(canonical_json_bytes(pairs)).hexdigest()


def load_audited_sql(spec: TableSpec) -> str:
    sql = spec.sql_path.read_text(encoding="utf-8")
    lowered = sql.casefold()
    collapsed = " ".join(lowered.split())
    if not lowered.startswith("select\n") or " from public." not in collapsed:
        raise RuntimeError("audited source SQL must be one explicit public SELECT")
    expected_bind_count = 1 if spec.book_scoped else 0
    if sql.count(":source_book_id") != expected_bind_count:
        raise RuntimeError("audited source SQL has an invalid Book bind")
    without_approved_bind = sql.replace(":source_book_id", "")
    if any(token in without_approved_bind for token in ("*", ":", "%s", "$1", "?")):
        raise RuntimeError("audited source SQL must be parameter-free and explicit")
    if any(
        token in lowered
        for token in (" insert ", " update ", " delete ", " alter ", " drop ")
    ):
        raise RuntimeError("audited source SQL may not write")
    return sql


def validate_result_columns(spec: TableSpec, columns: Sequence[str]) -> None:
    actual = tuple(columns)
    if len(actual) != len(set(actual)):
        raise ValueError("source query returned a duplicate result column")
    if actual != spec.columns:
        raise ValueError("source result columns do not match audited SQL")


def validate_source_url(source_url: str) -> None:
    try:
        parsed = make_url(source_url)
    except Exception:
        raise ValueError("source URL must use postgresql+psycopg") from None
    if parsed.drivername != "postgresql+psycopg" or not parsed.database:
        raise ValueError("source URL must use postgresql+psycopg with a database")


def validate_source_contract(row: Mapping[str, object]) -> None:
    expected_keys = {
        "source_revision",
        "attachments_relation",
        *(f"{spec.table}_count" for spec in TABLE_SPECS),
        *(
            f"{spec.table}_foreign_count"
            for spec in TABLE_SPECS
            if spec.book_scoped
        ),
    }
    valid = set(row) == expected_keys
    valid = valid and row.get("source_revision") == EXPECTED_SOURCE_REVISION
    valid = valid and row.get("attachments_relation") is None
    for spec in TABLE_SPECS:
        value = row.get(f"{spec.table}_count")
        valid = (
            valid
            and type(value) is int
            and value == EXPECTED_SOURCE_TABLE_COUNTS[spec.table]
        )
        if spec.book_scoped:
            foreign = row.get(f"{spec.table}_foreign_count")
            valid = valid and type(foreign) is int and foreign == 0
    if not valid:
        raise ValueError("global source contract does not match the fixed snapshot")


def load_source_contract_sql() -> str:
    sql = Path(__file__).with_name("sql").joinpath("source_contract.sql").read_text(
        encoding="utf-8"
    )
    collapsed = " ".join(sql.casefold().split())
    without_bind = sql.replace(":source_book_id", "").replace("::bigint", "")
    if (
        not collapsed.startswith("select ")
        or ":source_book_id" not in sql
        or ":" in without_bind
        or "select *" in collapsed
        or ";" in sql
        or any(
            token in f" {collapsed} "
            for token in (" insert ", " update ", " delete ", " alter ", " drop ")
        )
    ):
        raise RuntimeError("source contract SQL is not an audited read-only statement")
    return sql


def _status_is_on(value: object) -> bool:
    return str(value).casefold() in {"on", "true", "1"}


def _fetch_table(
    connection: Connection,
    spec: TableSpec,
    *,
    batch_size: int,
    source_book_id: str | None,
) -> list[dict[str, object]]:
    parameters = (
        {"source_book_id": source_book_id}
        if spec.book_scoped
        else None
    )
    result = connection.execute(text(load_audited_sql(spec)), parameters)
    validate_result_columns(spec, tuple(str(column) for column in result.keys()))
    mappings = result.mappings()
    rows: list[dict[str, object]] = []
    while batch := mappings.fetchmany(batch_size):
        rows.extend(dict(row) for row in batch)
    return rows


def _build_manifest(tables: Sequence[FrozenTableRows]) -> FrozenSourceManifest:
    table_manifests = tuple(
        TableManifest(
            table=table.table,
            row_count=len(table.rows),
            ndjson_sha256=table.ndjson_sha256,
            primary_key=_SPECS_BY_TABLE[table.table].primary_key,
        )
        for table in sorted(tables, key=lambda item: item.table)
    )
    provisional = FrozenSourceManifest(
        format_version=1,
        dump_sha256=EXPECTED_DUMP_SHA256,
        source_revision=EXPECTED_SOURCE_REVISION,
        snapshot_id="",
        tables=table_manifests,
        content_sha256="",
    )
    content_hash = provisional.calculated_content_sha256()
    return FrozenSourceManifest(
        format_version=1,
        dump_sha256=EXPECTED_DUMP_SHA256,
        source_revision=EXPECTED_SOURCE_REVISION,
        snapshot_id=f"sha256:{content_hash}",
        tables=table_manifests,
        content_sha256=content_hash,
    )


def extract_fixed_source(
    source_url: str,
    *,
    expected_manifest: FrozenSourceManifest,
    batch_size: int = 256,
    workers: int = 1,
    shuffle_seed: int = 0,
    table_order: Sequence[str] | None = None,
) -> FrozenSourceRows:
    assert_approved_manifest(expected_manifest)
    validate_source_url(source_url)
    if type(batch_size) is not int or batch_size <= 0:
        raise ValueError("source extraction batch size must be positive")
    if type(workers) is not int or workers <= 0:
        raise ValueError("source extraction worker count must be positive")
    if type(shuffle_seed) is not int:
        raise TypeError("source extraction shuffle seed must be an integer")
    schedule = (
        tuple(spec.table for spec in TABLE_SPECS)
        if table_order is None
        else tuple(table_order)
    )
    if len(schedule) != len(set(schedule)) or set(schedule) != set(_SPECS_BY_TABLE):
        raise ValueError("source extraction table schedule must cover audited tables")

    engine = create_engine(source_url, pool_pre_ping=True)
    raw_tables: dict[str, list[dict[str, object]]] = {}
    try:
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                transaction_read_only = connection.exec_driver_sql(
                    "SHOW transaction_read_only"
                ).scalar_one()
                default_read_only = connection.exec_driver_sql(
                    "SHOW default_transaction_read_only"
                ).scalar_one()
                if not _status_is_on(transaction_read_only):
                    raise RuntimeError("source extraction transaction is not read-only")
                if not _status_is_on(default_read_only):
                    raise RuntimeError("source server default is not read-only")
                ledger_spec = _SPECS_BY_TABLE["ledger_books"]
                ledger_rows = _fetch_table(
                    connection,
                    ledger_spec,
                    batch_size=batch_size,
                    source_book_id=None,
                )
                if len(ledger_rows) != 1:
                    raise ValueError("restored source must contain exactly one Book")
                source_book_id = ledger_rows[0].get("book_id")
                if type(source_book_id) is not str or not source_book_id.strip():
                    raise ValueError("restored source Book identity is invalid")
                raw_tables["ledger_books"] = ledger_rows
                contract_sql = load_source_contract_sql()
                contract_rows = tuple(
                    connection.execute(
                        text(contract_sql), {"source_book_id": source_book_id}
                    ).mappings()
                )
                if len(contract_rows) != 1:
                    raise ValueError("global source contract query returned invalid rows")
                validate_source_contract(contract_rows[0])
                for table in schedule:
                    if table == "ledger_books":
                        continue
                    raw_tables[table] = _fetch_table(
                        connection,
                        _SPECS_BY_TABLE[table],
                        batch_size=batch_size,
                        source_book_id=source_book_id,
                    )
    finally:
        engine.dispose()

    for table, rows in raw_tables.items():
        seed = hashlib.sha256(f"{shuffle_seed}:{table}".encode()).digest()
        random.Random(int.from_bytes(seed[:8], "big")).shuffle(rows)

    specs = tuple(_SPECS_BY_TABLE[table] for table in sorted(raw_tables))
    with ThreadPoolExecutor(max_workers=min(workers, len(specs))) as executor:
        frozen_tables = tuple(
            executor.map(
                lambda spec: canonicalize_table_rows(spec, raw_tables[spec.table]),
                specs,
            )
        )
    manifest = _build_manifest(frozen_tables)
    if manifest != expected_manifest:
        raise ValueError("source extraction does not match the approved manifest")
    result = FrozenSourceRows(
        manifest=manifest,
        tables=MappingProxyType(
            {table.table: table for table in sorted(frozen_tables, key=lambda t: t.table)}
        ),
        attachments_count=0,
    )
    verify_frozen_source_rows(result)
    return result


def verify_frozen_source_rows(source: FrozenSourceRows) -> None:
    assert_approved_manifest(source.manifest)
    if type(source.attachments_count) is not int or source.attachments_count != 0:
        raise ValueError("frozen source attachment proof is invalid")
    if set(source.tables) != set(_SPECS_BY_TABLE):
        raise ValueError("frozen source table coverage mismatch")
    table_manifests = {table.table: table for table in source.manifest.tables}
    for name, spec in _SPECS_BY_TABLE.items():
        table = source.tables[name]
        digest = verify_frozen_table_rows(spec, table)
        expected = table_manifests[name]
        if len(table.rows) != expected.row_count or digest != expected.ndjson_sha256:
            raise ValueError("frozen source table does not match approved manifest")
    if account_uuid_map_digest(source.tables["accounts"].rows) != (
        EXPECTED_SOURCE_TABLE_COUNTS["accounts"],
        ACCOUNT_UUID_MAP_SHA256,
    ):
        raise ValueError("frozen source account UUID aggregate mismatch")


__all__ = [
    "FrozenSourceRows",
    "FrozenTableRows",
    "TABLE_SPECS",
    "TableSpec",
    "ACCOUNT_UUID_MAP_PROTOCOL",
    "ACCOUNT_UUID_MAP_SHA256",
    "account_uuid_map_digest",
    "canonicalize_table_rows",
    "canonicalize_value",
    "extract_fixed_source",
    "load_audited_sql",
    "load_source_contract_sql",
    "validate_result_columns",
    "validate_source_contract",
    "validate_source_url",
    "verify_frozen_source_rows",
    "verify_frozen_table_rows",
]
