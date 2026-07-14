from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import Connection, create_engine, text


_ALLOWED_NONEMPTY_TARGET_TABLES = frozenset(
    {
        "alembic_version",
        "projection_generations",
        "synchronous_projection_event_types",
        "v2_schema_metadata",
    }
)


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


@dataclass(frozen=True, order=True)
class TableManifest:
    table: str
    row_count: int
    ndjson_sha256: str
    primary_key: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "ndjson_sha256": self.ndjson_sha256,
            "primary_key": list(self.primary_key),
            "row_count": self.row_count,
            "table": self.table,
        }


@dataclass(frozen=True)
class FrozenSourceManifest:
    format_version: int
    dump_sha256: str
    source_revision: str
    snapshot_id: str
    tables: tuple[TableManifest, ...]
    content_sha256: str = ""

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

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FrozenSourceManifest:
        tables = tuple(
            TableManifest(
                table=str(item["table"]),
                row_count=int(item["row_count"]),
                ndjson_sha256=str(item["ndjson_sha256"]),
                primary_key=tuple(str(value) for value in item.get("primary_key", ())),
            )
            for item in raw.get("tables", ())
        )
        return cls(
            format_version=int(raw.get("format_version", 1)),
            dump_sha256=str(raw["dump_sha256"]),
            source_revision=str(raw["source_revision"]),
            snapshot_id=str(raw.get("snapshot_id", "")),
            tables=tables,
            content_sha256=str(raw.get("content_sha256", "")),
        )


def build_manifest(
    *,
    dump_sha256: str,
    source_revision: str,
    tables: tuple[TableManifest, ...],
) -> FrozenSourceManifest:
    provisional = FrozenSourceManifest(
        format_version=1,
        dump_sha256=dump_sha256,
        source_revision=source_revision,
        snapshot_id="",
        tables=tuple(sorted(tables)),
    )
    content_sha256 = provisional.calculated_content_sha256()
    return FrozenSourceManifest(
        format_version=provisional.format_version,
        dump_sha256=dump_sha256,
        source_revision=source_revision,
        snapshot_id=f"sha256:{content_sha256}",
        tables=provisional.tables,
        content_sha256=content_sha256,
    )


def write_manifest(manifest: FrozenSourceManifest, path: Path) -> None:
    payload = canonical_json_bytes(manifest.to_dict()) + b"\n"
    Path(path).write_bytes(payload)


def read_manifest(path: Path) -> FrozenSourceManifest:
    content = Path(path).read_text(encoding="utf-8")
    stripped = content.lstrip()
    if stripped.startswith("{"):
        manifest = FrozenSourceManifest.from_dict(json.loads(content))
    else:
        values: dict[str, str] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator:
                key, separator, value = line.partition(":")
            if separator:
                values[key.strip().casefold().replace("-", "_")] = value.strip()
        dump_sha256 = next(
            (
                values[key]
                for key in ("dump_sha256", "dump_sha256sum", "sha256")
                if key in values
            ),
            "",
        )
        source_revision = next(
            (
                values[key]
                for key in ("source_revision", "alembic_revision", "revision")
                if key in values
            ),
            "",
        )
        if not dump_sha256 or not source_revision:
            raise ValueError("manifest must contain dump SHA-256 and source revision")
        manifest = FrozenSourceManifest(
            format_version=1,
            dump_sha256=dump_sha256,
            source_revision=source_revision,
            snapshot_id=values.get("snapshot_id", f"sha256:{dump_sha256}"),
            tables=(),
        )

    if manifest.content_sha256:
        actual = manifest.calculated_content_sha256()
        if actual != manifest.content_sha256:
            raise ValueError("manifest content SHA-256 does not match its contents")
        if manifest.snapshot_id != f"sha256:{actual}":
            raise ValueError("manifest snapshot ID does not match its contents")
    return manifest


def verify_frozen_source(
    *,
    dump_path: Path,
    manifest: FrozenSourceManifest,
    actual_source_revision: str,
) -> None:
    actual_dump_sha256 = sha256_file(dump_path)
    if actual_dump_sha256 != manifest.dump_sha256:
        raise ValueError("frozen dump SHA-256 does not match the manifest")
    if actual_source_revision != manifest.source_revision:
        raise ValueError("restored source revision does not match the manifest")


def validate_target_state(
    *,
    actual_revision: str,
    expected_revision: str,
    row_counts: Mapping[str, int],
) -> None:
    if actual_revision != expected_revision:
        raise ValueError(
            "target is not at the exact V2 Alembic head "
            f"(expected {expected_revision}, found {actual_revision})"
        )
    nonempty = {
        table: count
        for table, count in row_counts.items()
        if count and table not in _ALLOWED_NONEMPTY_TARGET_TABLES
    }
    if nonempty:
        detail = ", ".join(f"{table}={nonempty[table]}" for table in sorted(nonempty))
        raise ValueError(f"target is not empty: {detail}")


def read_target_state(connection: Connection) -> tuple[str, dict[str, int]]:
    revisions = tuple(
        connection.execute(text("select version_num from public.alembic_version"))
        .scalars()
        .all()
    )
    if len(revisions) != 1:
        raise ValueError("target must contain exactly one Alembic revision")
    tables = tuple(
        connection.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' and table_type = 'BASE TABLE' "
                "order by table_name"
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for table in tables:
        if not isinstance(table, str) or not table.replace("_", "").isalnum():
            raise ValueError("target contains an unsafe table identifier")
        counts[table] = int(
            connection.execute(
                text(f'SELECT count(*) FROM public."{table}"')
            ).scalar_one()
        )
    return str(revisions[0]), counts


def assert_target_ready(target_url: str, *, expected_revision: str) -> None:
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            actual_revision, row_counts = read_target_state(connection)
            validate_target_state(
                actual_revision=actual_revision,
                expected_revision=expected_revision,
                row_counts=row_counts,
            )
    finally:
        engine.dispose()


__all__ = [
    "FrozenSourceManifest",
    "TableManifest",
    "assert_target_ready",
    "build_manifest",
    "canonical_json_bytes",
    "read_manifest",
    "read_target_state",
    "sha256_file",
    "validate_target_state",
    "verify_frozen_source",
    "write_manifest",
]
