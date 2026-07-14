from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence
from uuid import UUID

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from .reference_reducer import (
    VerificationIssue,
    VerificationReport,
    canonical_json_bytes,
    reduce_target,
)


_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

_TARGET_TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "assets": (
        (
            "asset_code",
            "kind",
            "ledger_scale",
            "input_scale",
            "display_scale",
            "current_name",
            "status",
        ),
        ("asset_code",),
    ),
    "books": (
        ("book_id", "current_name", "base_asset_code", "write_state"),
        ("book_id",),
    ),
    "accounts": (
        (
            "book_id",
            "account_id",
            "asset_code",
            "account_type",
            "system_role",
            "current_name",
            "status",
        ),
        ("book_id", "account_id"),
    ),
    "categories": (
        (
            "book_id",
            "category_id",
            "parent_category_id",
            "current_name",
            "current_version_id",
            "status",
        ),
        ("book_id", "category_id"),
    ),
    "ledger_events": (
        (
            "event_id",
            "global_sequence",
            "book_id",
            "book_position",
            "stream_type",
            "stream_id",
            "stream_version",
            "event_type",
            "event_schema_version",
            "command_id",
            "actor_subject_id",
            "correlation_id",
            "causation_event_id",
            "effective_at",
            "recorded_at",
            "payload",
            "previous_hash",
            "event_hash",
        ),
        ("book_id", "book_position"),
    ),
    "book_event_heads": (
        ("book_id", "last_position", "last_hash"),
        ("book_id",),
    ),
    "event_stream_heads": (
        (
            "book_id",
            "stream_type",
            "stream_id",
            "last_version",
            "last_book_position",
            "last_event_id",
        ),
        ("book_id", "stream_type", "stream_id"),
    ),
    "journal_transactions": (
        (
            "book_id",
            "transaction_id",
            "source_event_id",
            "source_position",
            "effective_at",
            "transaction_kind",
            "description_ref",
        ),
        ("book_id", "transaction_id"),
    ),
    "journal_postings": (
        (
            "book_id",
            "transaction_id",
            "posting_id",
            "posting_position",
            "account_id",
            "asset_code",
            "side",
            "units",
        ),
        ("book_id", "transaction_id", "posting_position", "posting_id"),
    ),
    "account_balances": (
        (
            "book_id",
            "account_id",
            "asset_code",
            "balance_units",
            "as_of_position",
        ),
        ("book_id", "account_id", "asset_code"),
    ),
    "transaction_reversals": (
        (
            "book_id",
            "reversal_transaction_id",
            "original_transaction_id",
            "source_event_id",
            "original_event_id",
            "original_event_hash",
            "reason_code",
        ),
        ("book_id", "reversal_transaction_id"),
    ),
    "reporting_lines": (
        (
            "book_id",
            "transaction_id",
            "classification_revision",
            "line_id",
            "line_version_id",
            "catalog_id",
            "line_position",
            "asset_code",
            "units",
            "line_kind",
            "dimension",
            "dimension_id",
            "description_ref",
            "source_event_id",
        ),
        (
            "book_id",
            "transaction_id",
            "classification_revision",
            "line_position",
        ),
    ),
    "investment_lots": (
        (
            "book_id",
            "lot_id",
            "acquisition_transaction_id",
            "instrument_asset_code",
            "settlement_asset_code",
            "acquired_quantity_units",
            "acquired_cost_units",
            "fee_units",
            "remaining_quantity_units",
            "remaining_cost_units",
            "source_event_id",
            "source_position",
        ),
        ("book_id", "lot_id"),
    ),
    "investment_lot_allocations": (
        (
            "book_id",
            "allocation_id",
            "lot_id",
            "disposal_transaction_id",
            "allocation_position",
            "quantity_units",
            "cost_units",
            "source_event_id",
            "source_position",
        ),
        ("book_id", "allocation_id"),
    ),
}

_SOURCE_TO_TARGET_COUNTS = {
    "accounts": "accounts",
    "assets": "assets",
    "categories": "categories",
    "ledger_books": "books",
    "postings": "journal_postings",
    "transaction_lines": "reporting_lines",
    "transactions": "journal_transactions",
}

_SOURCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "assets": (
        "asset_code",
        "kind",
        "scale",
        "display_scale",
        "name",
        "status",
        "version",
    ),
    "ledger_books": (
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
    "accounts": (
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
    "categories": (
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
    "category_versions": (
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
    "transactions": (
        "transaction_id",
        "book_id",
        "memo",
        "occurred_at",
        "purpose",
        "reversed_by",
        "reverses_transaction_id",
        "version",
    ),
    "postings": (
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
    "transaction_lines": (
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
    "classification_events": (
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
    "investment_events": (
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
    "investment_valuations": (
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
}


@dataclass(frozen=True, slots=True)
class _ManifestTable:
    table: str
    row_count: int
    ndjson_sha256: str
    primary_key: tuple[str, ...]


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value) or len(value.encode("ascii")) > 63:
        raise ValueError(f"unsafe SQL identifier in verifier: {value!r}")
    return value


def _table_exists(connection: Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                "select exists (select 1 from information_schema.tables "
                "where table_schema='public' and table_name=:table_name "
                "and table_type='BASE TABLE')"
            ),
            {"table_name": table_name},
        ).scalar_one()
    )


def _read_rows(
    connection: Connection,
    table_name: str,
    columns: tuple[str, ...],
    order_by: tuple[str, ...],
) -> list[Mapping[str, object]]:
    table = _safe_identifier(table_name)
    rendered_columns = ", ".join(_safe_identifier(column) for column in columns)
    rendered_order = ", ".join(_safe_identifier(column) for column in order_by)
    statement = f"select {rendered_columns} from public.{table}"
    if rendered_order:
        statement += f" order by {rendered_order}"
    return [dict(row) for row in connection.execute(text(statement)).mappings()]


def _read_target(connection: Connection) -> dict[str, list[Mapping[str, object]]]:
    missing = [
        table_name
        for table_name in _TARGET_TABLES
        if not _table_exists(connection, table_name)
    ]
    if missing:
        raise ValueError(f"target is missing V2 table(s): {', '.join(sorted(missing))}")
    return {
        table_name: _read_rows(connection, table_name, columns, order_by)
        for table_name, (columns, order_by) in _TARGET_TABLES.items()
    }


def verify_target(target_url: str) -> VerificationReport:
    """Independently verify immutable events and synchronous projections by SQL."""

    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                raise ValueError("independent verifier requires PostgreSQL")
            rows = _read_target(connection)
        return reduce_target(rows)
    finally:
        engine.dispose()


def _database_identity(url: str) -> tuple[str, int, str]:
    parsed = make_url(url)
    if not parsed.host or not parsed.database:
        raise ValueError("verifier database URL must include host and database")
    host = parsed.host.casefold()
    if host in {"127.0.0.1", "::1", "localhost"}:
        host = "loopback"
    return host, parsed.port or 5432, parsed.database


def _read_manifest(
    path: Path,
) -> tuple[dict[str, object], str, str, str, tuple[_ManifestTable, ...]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("manifest must be readable canonical JSON") from error
    if type(raw) is not dict:
        raise ValueError("manifest must contain a JSON object")
    required = {"dump_sha256", "format_version", "source_revision", "tables"}
    if not required <= raw.keys() or type(raw["tables"]) is not list:
        raise ValueError("manifest is missing its frozen source contract")
    content = {
        "dump_sha256": raw["dump_sha256"],
        "format_version": raw["format_version"],
        "source_revision": raw["source_revision"],
        "tables": raw["tables"],
    }
    calculated = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    declared = raw.get("content_sha256")
    if declared is not None and declared != calculated:
        raise ValueError("manifest content SHA-256 mismatch")
    snapshot_id = str(raw.get("snapshot_id", f"sha256:{calculated}"))
    if snapshot_id != f"sha256:{calculated}":
        raise ValueError("manifest snapshot ID mismatch")
    tables: list[_ManifestTable] = []
    names: set[str] = set()
    for table in raw["tables"]:
        if type(table) is not dict:
            raise ValueError("manifest table entry must be an object")
        name = _safe_identifier(str(table.get("table", "")))
        count = table.get("row_count")
        digest = table.get("ndjson_sha256")
        raw_primary_key = table.get("primary_key", [])
        if (
            type(count) is not int
            or count < 0
            or name in names
            or type(digest) is not str
            or len(digest) != 64
            or type(raw_primary_key) is not list
        ):
            raise ValueError(
                "manifest table counts must be unique nonnegative integers"
            )
        try:
            bytes.fromhex(digest)
        except ValueError:
            raise ValueError(
                "manifest table hash must be hexadecimal SHA-256"
            ) from None
        primary_key = tuple(_safe_identifier(str(column)) for column in raw_primary_key)
        if name not in _SOURCE_COLUMNS or any(
            column not in _SOURCE_COLUMNS[name] for column in primary_key
        ):
            raise ValueError(f"manifest contains an unknown source contract: {name}")
        names.add(name)
        tables.append(
            _ManifestTable(
                table=name,
                row_count=count,
                ndjson_sha256=digest.lower(),
                primary_key=primary_key,
            )
        )
    return (
        raw,
        calculated,
        snapshot_id,
        str(raw["source_revision"]),
        tuple(tables),
    )


def _canonical_source_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("source contains a non-finite decimal")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("source contains a non-finite float")
        return format(Decimal(repr(value)), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source contains a naive datetime")
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
            str(key): _canonical_source_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_source_value(item) for item in value]
    raise TypeError(f"unsupported source value type: {type(value).__name__}")


def _canonical_source_row(row: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _canonical_source_value(value)
        for key, value in sorted(row.items(), key=lambda pair: str(pair[0]))
    }


def _source_table_hash(
    rows: list[Mapping[str, object]], *, primary_key: tuple[str, ...]
) -> str:
    canonical_rows = [_canonical_source_row(row) for row in rows]
    if primary_key:
        canonical_rows.sort(
            key=lambda row: tuple(
                canonical_json_bytes(row[column]) for column in primary_key
            )
        )
    else:
        canonical_rows.sort(key=canonical_json_bytes)
    digest = hashlib.sha256()
    for row in canonical_rows:
        digest.update(canonical_json_bytes(row) + b"\n")
    return digest.hexdigest()


def _read_source_facts(
    source_url: str, expected: tuple[_ManifestTable, ...]
) -> tuple[str, dict[str, int], dict[str, str]]:
    engine = create_engine(source_url, pool_pre_ping=True)
    try:
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                read_only = connection.exec_driver_sql(
                    "SHOW transaction_read_only"
                ).scalar_one()
                if str(read_only).casefold() not in {"on", "true", "1"}:
                    raise RuntimeError(
                        "source verification transaction is not read-only"
                    )
                revisions = tuple(
                    str(value)
                    for value in connection.execute(
                        text("select version_num from public.alembic_version")
                    ).scalars()
                )
                if len(revisions) != 1:
                    raise ValueError("source must contain exactly one Alembic revision")
                counts: dict[str, int] = {}
                hashes: dict[str, str] = {}
                for table in sorted(expected, key=lambda item: item.table):
                    rendered_columns = ", ".join(
                        _safe_identifier(column)
                        for column in _SOURCE_COLUMNS[table.table]
                    )
                    records = [
                        dict(row)
                        for row in connection.execute(
                            text(
                                f"select {rendered_columns} "
                                f"from public.{_safe_identifier(table.table)}"
                            )
                        ).mappings()
                    ]
                    counts[table.table] = len(records)
                    hashes[table.table] = _source_table_hash(
                        records, primary_key=table.primary_key
                    )
        return revisions[0], counts, hashes
    finally:
        engine.dispose()


def _read_backfill_controls(
    target_url: str, snapshot_id: str
) -> tuple[Counter[str], int, list[Mapping[str, object]]]:
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            for table in (
                "backfill_source_receipts",
                "backfill_quarantine",
                "backfill_seals",
            ):
                if not _table_exists(connection, table):
                    raise ValueError(
                        f"target is missing backfill control table: {table}"
                    )
            receipts = Counter(
                {
                    str(row.source_table): int(row.row_count)
                    for row in connection.execute(
                        text(
                            "select source_table, count(*) as row_count "
                            "from public.backfill_source_receipts "
                            "where snapshot_id=:snapshot_id group by source_table"
                        ),
                        {"snapshot_id": snapshot_id},
                    )
                }
            )
            quarantine_count = int(
                connection.execute(
                    text(
                        "select count(*) from public.backfill_quarantine "
                        "where snapshot_id=:snapshot_id"
                    ),
                    {"snapshot_id": snapshot_id},
                ).scalar_one()
            )
            seals = [
                dict(row)
                for row in connection.execute(
                    text(
                        "select snapshot_id, manifest_hash, source_counts, "
                        "terminal_book_hashes, quarantine_count, receipt_count "
                        "from public.backfill_seals where snapshot_id=:snapshot_id"
                    ),
                    {"snapshot_id": snapshot_id},
                ).mappings()
            ]
        return receipts, quarantine_count, seals
    finally:
        engine.dispose()


def verify_backfill(
    *,
    source_url: str,
    target_url: str,
    manifest_path: Path,
    output_path: Path | None = None,
) -> VerificationReport:
    if _database_identity(source_url) == _database_identity(target_url):
        raise ValueError("source and target must be different databases")
    _, manifest_hash, snapshot_id, expected_revision, manifest_tables = _read_manifest(
        manifest_path
    )
    expected_counts = {table.table: table.row_count for table in manifest_tables}
    source_revision, source_counts, source_hashes = _read_source_facts(
        source_url, manifest_tables
    )
    target_report = verify_target(target_url)
    issues = list(target_report.issues)
    if source_revision != expected_revision:
        issues.append(
            VerificationIssue(
                "source_revision_mismatch",
                "source:alembic_version",
                f"manifest={expected_revision} source={source_revision}",
            )
        )
    manifest_hashes = {table.table: table.ndjson_sha256 for table in manifest_tables}
    for table_name in sorted(expected_counts):
        if source_counts[table_name] != expected_counts[table_name]:
            issues.append(
                VerificationIssue(
                    "source_count_mismatch",
                    f"source-table:{table_name}",
                    f"manifest={expected_counts[table_name]} source={source_counts[table_name]}",
                )
            )
        if source_hashes[table_name] != manifest_hashes[table_name]:
            issues.append(
                VerificationIssue(
                    "source_content_mismatch",
                    f"source-table:{table_name}",
                    "direct source SQL does not match the frozen table hash",
                )
            )
    for source_table, target_table in sorted(_SOURCE_TO_TARGET_COUNTS.items()):
        if source_table in source_counts and (
            source_counts[source_table] != target_report.counts.get(target_table, 0)
        ):
            issues.append(
                VerificationIssue(
                    "source_target_count_mismatch",
                    f"mapping:{source_table}->{target_table}",
                    "source and target fact counts differ",
                )
            )

    receipts, quarantine_count, seals = _read_backfill_controls(target_url, snapshot_id)
    receipt_count = sum(receipts.values())
    for table_name, count in sorted(expected_counts.items()):
        if receipts[table_name] != count:
            issues.append(
                VerificationIssue(
                    "source_receipt_count_mismatch",
                    f"source-table:{table_name}",
                    f"expected {count} receipts, found {receipts[table_name]}",
                )
            )
    for table_name in sorted(set(receipts) - set(expected_counts)):
        issues.append(
            VerificationIssue(
                "source_receipt_unexpected",
                f"source-table:{table_name}",
                f"found {receipts[table_name]} receipts for an unmanifested table",
            )
        )
    if receipt_count != sum(source_counts.values()):
        issues.append(
            VerificationIssue(
                "source_receipt_count_mismatch",
                f"snapshot:{snapshot_id}",
                "receipt total differs from direct source SQL counts",
            )
        )
    if quarantine_count:
        issues.append(
            VerificationIssue(
                "quarantine_nonzero",
                f"snapshot:{snapshot_id}",
                f"{quarantine_count} source rows are quarantined",
            )
        )
    if len(seals) != 1:
        issues.append(
            VerificationIssue(
                "backfill_seal_missing",
                f"snapshot:{snapshot_id}",
                f"expected one seal, found {len(seals)}",
            )
        )
    else:
        seal = seals[0]
        expected_manifest_bytes = bytes.fromhex(manifest_hash)
        if seal["manifest_hash"] != expected_manifest_bytes:
            issues.append(
                VerificationIssue(
                    "backfill_seal_mismatch",
                    f"snapshot:{snapshot_id}",
                    "seal manifest hash differs from independently parsed manifest",
                )
            )
        if dict(seal["source_counts"]) != dict(sorted(source_counts.items())):
            issues.append(
                VerificationIssue(
                    "backfill_seal_mismatch",
                    f"snapshot:{snapshot_id}",
                    "seal source counts differ from direct source SQL",
                )
            )
        if (
            int(seal["receipt_count"]) != receipt_count
            or int(seal["quarantine_count"]) != quarantine_count
        ):
            issues.append(
                VerificationIssue(
                    "backfill_seal_mismatch",
                    f"snapshot:{snapshot_id}",
                    "seal control totals differ from direct target SQL",
                )
            )
        if dict(seal["terminal_book_hashes"]) != target_report.book_terminal_hashes:
            issues.append(
                VerificationIssue(
                    "backfill_seal_mismatch",
                    f"snapshot:{snapshot_id}",
                    "seal terminal hashes differ from independent chain reduction",
                )
            )

    report = replace(
        target_report,
        issues=tuple(sorted(set(issues))),
        source_counts=dict(sorted(source_counts.items())),
        receipt_count=receipt_count,
        quarantine_count=quarantine_count,
        manifest_hash=manifest_hash,
        snapshot_id=snapshot_id,
    )
    if output_path is not None:
        output = Path(output_path)
        if output.exists():
            raise FileExistsError(f"verification output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_json_bytes(report.to_dict()) + b"\n")
    return report


def verify(
    *,
    source_url: str,
    target_url: str,
    manifest_path: Path,
    output_path: Path | None = None,
) -> VerificationReport:
    return verify_backfill(
        source_url=source_url,
        target_url=target_url,
        manifest_path=manifest_path,
        output_path=output_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.tools.backfill_v1.verify",
        description="Independent SQL/reference verifier for one V1-to-V2 backfill",
    )
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        report = verify_backfill(
            source_url=args.source_url,
            target_url=args.target_url,
            manifest_path=args.manifest,
            output_path=args.output,
        )
    except (FileExistsError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "verify", "verify_backfill", "verify_target"]
