from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .constants import (
    EXPECTED_DUMP_BYTES,
    EXPECTED_DUMP_SHA256,
    EXPECTED_FULL_MANIFEST_SHA256,
    EXPECTED_SIMPLE_MANIFEST_COUNTS,
    EXPECTED_SOURCE_REVISION,
    EXPECTED_SOURCE_PRIMARY_KEYS,
    EXPECTED_SOURCE_TABLE_COUNTS,
    FROZEN_SOURCE_ARTIFACT,
)


_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SIMPLE_KEYS = frozenset(
    {
        "artifact",
        "created_at",
        "verified_at",
        "sha256",
        "bytes",
        "archive_entries",
        "source_database",
        "source_schema",
        "source_runtime_revision",
        "source_alembic_revision",
        *EXPECTED_SIMPLE_MANIFEST_COUNTS,
        "restore_test",
        "restore_postgres_version",
        "restore_database",
    }
)
_EXPECTED_SIMPLE_METADATA = {
    "created_at": "2026-07-13T01:56:34Z",
    "verified_at": "2026-07-13T02:00:26Z",
    "archive_entries": "140",
    "source_database": "track_anywhere",
    "source_schema": "public",
    "source_runtime_revision": "ed52ac2",
    "restore_test": "passed",
    "restore_postgres_version": "17",
    "restore_database": "track_anywhere_restore",
}


@dataclass(frozen=True, slots=True)
class SimpleBackupManifest:
    artifact: str
    dump_sha256: str
    dump_bytes: int
    source_revision: str
    counts: Mapping[str, int]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, order=True, slots=True)
class TableManifest:
    table: str
    row_count: int
    ndjson_sha256: str
    primary_key: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ndjson_sha256": self.ndjson_sha256,
            "primary_key": list(self.primary_key),
            "row_count": self.row_count,
            "table": self.table,
        }


@dataclass(frozen=True, slots=True)
class FrozenSourceManifest:
    format_version: int
    dump_sha256: str
    source_revision: str
    snapshot_id: str
    tables: tuple[TableManifest, ...]
    content_sha256: str

    def content_payload(self) -> dict[str, object]:
        return {
            "dump_sha256": self.dump_sha256,
            "format_version": self.format_version,
            "source_revision": self.source_revision,
            "tables": [table.to_dict() for table in sorted(self.tables)],
        }

    def calculated_content_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.content_payload())).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.content_payload(),
            "content_sha256": self.content_sha256,
            "snapshot_id": self.snapshot_id,
        }


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _lower_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or _LOWER_SHA256.fullmatch(value) is None:
        raise ValueError(f"full manifest {field} must be a lowercase SHA-256")
    return value


def read_full_manifest(path: Path) -> FrozenSourceManifest:
    try:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("full manifest must be readable strict JSON") from error
    if type(raw) is not dict or set(raw) != {
        "content_sha256",
        "dump_sha256",
        "format_version",
        "snapshot_id",
        "source_revision",
        "tables",
    }:
        raise ValueError("full manifest fields do not match schema version 1")
    if type(raw["format_version"]) is not int or raw["format_version"] != 1:
        raise ValueError("full manifest format version is unsupported")
    dump_sha256 = _lower_sha256(raw["dump_sha256"], field="dump hash")
    if dump_sha256 != EXPECTED_DUMP_SHA256:
        raise ValueError("full manifest fixed dump hash mismatch")
    if raw["source_revision"] != EXPECTED_SOURCE_REVISION:
        raise ValueError("full manifest source revision mismatch")
    content_sha256 = _lower_sha256(raw["content_sha256"], field="content hash")
    snapshot_id = raw["snapshot_id"]
    if type(snapshot_id) is not str:
        raise ValueError("full manifest snapshot ID is invalid")

    tables_raw = raw["tables"]
    if type(tables_raw) is not list:
        raise ValueError("full manifest tables must be an array")
    tables: list[TableManifest] = []
    seen_tables: set[str] = set()
    for item in tables_raw:
        if type(item) is not dict or set(item) != {
            "ndjson_sha256",
            "primary_key",
            "row_count",
            "table",
        }:
            raise ValueError("full manifest table fields do not match schema")
        table = item["table"]
        if type(table) is not str or table not in EXPECTED_SOURCE_TABLE_COUNTS:
            raise ValueError("full manifest contains an unknown table")
        if table in seen_tables:
            raise ValueError("full manifest contains a duplicate table")
        seen_tables.add(table)
        row_count = item["row_count"]
        if type(row_count) is not int or row_count < 0:
            raise ValueError("full manifest table count must be a nonnegative integer")
        if row_count != EXPECTED_SOURCE_TABLE_COUNTS[table]:
            raise ValueError("full manifest fixed table count mismatch")
        primary_key_raw = item["primary_key"]
        if type(primary_key_raw) is not list or any(
            type(column) is not str or not column for column in primary_key_raw
        ):
            raise ValueError("full manifest primary key is invalid")
        primary_key = tuple(primary_key_raw)
        if primary_key != EXPECTED_SOURCE_PRIMARY_KEYS[table]:
            raise ValueError("full manifest fixed primary key mismatch")
        tables.append(
            TableManifest(
                table=table,
                row_count=row_count,
                ndjson_sha256=_lower_sha256(
                    item["ndjson_sha256"], field="table hash"
                ),
                primary_key=primary_key,
            )
        )
    if seen_tables != set(EXPECTED_SOURCE_TABLE_COUNTS):
        raise ValueError("full manifest table coverage mismatch")
    if [table.table for table in tables] != sorted(seen_tables):
        raise ValueError("full manifest tables are not in canonical order")

    manifest = FrozenSourceManifest(
        format_version=1,
        dump_sha256=dump_sha256,
        source_revision=EXPECTED_SOURCE_REVISION,
        snapshot_id=snapshot_id,
        tables=tuple(tables),
        content_sha256=content_sha256,
    )
    calculated = manifest.calculated_content_sha256()
    if calculated != content_sha256:
        raise ValueError("full manifest content SHA-256 mismatch")
    if content_sha256 != EXPECTED_FULL_MANIFEST_SHA256:
        raise ValueError("full manifest is not the approved frozen manifest")
    if snapshot_id != f"sha256:{content_sha256}":
        raise ValueError("full manifest snapshot ID mismatch")
    return manifest


def assert_approved_manifest(manifest: FrozenSourceManifest) -> None:
    try:
        approved = (
            type(manifest) is FrozenSourceManifest
            and manifest.format_version == 1
            and manifest.dump_sha256 == EXPECTED_DUMP_SHA256
            and manifest.source_revision == EXPECTED_SOURCE_REVISION
            and manifest.content_sha256 == EXPECTED_FULL_MANIFEST_SHA256
            and manifest.snapshot_id == f"sha256:{EXPECTED_FULL_MANIFEST_SHA256}"
            and manifest.calculated_content_sha256()
            == EXPECTED_FULL_MANIFEST_SHA256
            and {table.table for table in manifest.tables}
            == set(EXPECTED_SOURCE_TABLE_COUNTS)
            and all(
                table.row_count == EXPECTED_SOURCE_TABLE_COUNTS[table.table]
                and table.primary_key == EXPECTED_SOURCE_PRIMARY_KEYS[table.table]
                and _LOWER_SHA256.fullmatch(table.ndjson_sha256) is not None
                for table in manifest.tables
            )
        )
    except (KeyError, TypeError, ValueError):
        approved = False
    if not approved:
        raise ValueError("manifest is not the approved frozen manifest")


def _parse_positive_int(value: str, *, field: str, allow_zero: bool = False) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"manifest {field} must be an unsigned decimal integer")
    parsed = int(value)
    if parsed < (0 if allow_zero else 1):
        raise ValueError(f"manifest {field} is outside its allowed range")
    return parsed


def read_simple_manifest(path: Path) -> SimpleBackupManifest:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw_line:
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or not key or not value:
            raise ValueError("malformed simple manifest line")
        if key not in _SIMPLE_KEYS:
            raise ValueError("unknown manifest key")
        if key in values:
            raise ValueError("duplicate manifest key")
        values[key] = value

    missing = _SIMPLE_KEYS.difference(values)
    if missing:
        raise ValueError("simple manifest is missing required keys")

    if values["artifact"] != FROZEN_SOURCE_ARTIFACT:
        raise ValueError("simple manifest does not identify the fixed artifact")
    dump_sha256 = values["sha256"]
    if (
        _LOWER_SHA256.fullmatch(dump_sha256) is None
        or dump_sha256 != EXPECTED_DUMP_SHA256
    ):
        raise ValueError("simple manifest fixed dump hash mismatch")
    dump_bytes = _parse_positive_int(values["bytes"], field="bytes")
    if dump_bytes != EXPECTED_DUMP_BYTES:
        raise ValueError("simple manifest fixed dump size mismatch")
    source_revision = values["source_alembic_revision"]
    if source_revision != EXPECTED_SOURCE_REVISION:
        raise ValueError("simple manifest source revision mismatch")
    if any(
        values[field] != expected
        for field, expected in _EXPECTED_SIMPLE_METADATA.items()
    ):
        raise ValueError("simple manifest fixed metadata mismatch")

    counts: dict[str, int] = {}
    for name, expected in EXPECTED_SIMPLE_MANIFEST_COUNTS.items():
        actual = _parse_positive_int(values[name], field=name)
        if actual != expected:
            raise ValueError("simple manifest fixed table count mismatch")
        counts[name] = actual

    return SimpleBackupManifest(
        artifact=values["artifact"],
        dump_sha256=dump_sha256,
        dump_bytes=dump_bytes,
        source_revision=source_revision,
        counts=MappingProxyType(counts),
    )


__all__ = [
    "FrozenSourceManifest",
    "SimpleBackupManifest",
    "TableManifest",
    "canonical_json_bytes",
    "assert_approved_manifest",
    "read_full_manifest",
    "read_simple_manifest",
    "sha256_file",
]
