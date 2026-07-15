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
from uuid import UUID, uuid5

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from .credit_card_review import (
    CreditCardSemanticReview,
    credit_card_transaction_scope,
    read_credit_card_review,
)
from .manifest import read_manifest
from .reference_reducer import (
    VerificationIssue,
    VerificationReport,
    canonical_json_bytes,
    reference_backfill_receipts,
    reduce_target,
    verify_source_target_semantics,
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
            "account_subtype",
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
    "category_versions": (
        (
            "book_id",
            "category_id",
            "category_version_id",
            "parent_category_id",
            "name",
            "status",
            "change_reason_code",
        ),
        ("book_id", "category_id", "category_version_id"),
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
    "credit_card_transactions": (
        (
            "book_id",
            "transaction_id",
            "intent",
            "card_account_id",
            "counter_account_id",
            "asset_code",
            "units",
            "original_transaction_id",
            "source_event_id",
            "source_position",
        ),
        ("book_id", "source_position", "transaction_id"),
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
            "counterparty_id",
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
    "counterparties": (
        "counterparty_id",
        "book_id",
        "slug",
        "name",
        "kind",
        "status",
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


def _read_target_facts(
    target_url: str,
) -> tuple[dict[str, list[Mapping[str, object]]], VerificationReport]:
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                raise ValueError("independent verifier requires PostgreSQL")
            rows = _read_target(connection)
        return rows, reduce_target(rows)
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
) -> tuple[
    str,
    dict[str, int],
    dict[str, str],
    dict[str, list[Mapping[str, object]]],
]:
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
                rows_by_table: dict[str, list[Mapping[str, object]]] = {}
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
                    rows_by_table[table.table] = records
        return revisions[0], counts, hashes, rows_by_table
    finally:
        engine.dispose()


def _read_backfill_controls(
    target_url: str, snapshot_id: str
) -> tuple[
    list[Mapping[str, object]],
    int,
    list[Mapping[str, object]],
    list[Mapping[str, object]],
]:
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            for table in (
                "backfill_source_receipts",
                "backfill_quarantine",
                "backfill_review_contracts",
                "backfill_seals",
            ):
                if not _table_exists(connection, table):
                    raise ValueError(
                        f"target is missing backfill control table: {table}"
                    )
            receipts = [
                dict(row)
                for row in connection.execute(
                    text(
                        "select snapshot_id, source_table, source_primary_key, "
                        "canonical_source_key, book_id, source_hash, "
                        "target_entity_id from public.backfill_source_receipts "
                        "where snapshot_id=:snapshot_id "
                        "order by source_table, source_primary_key"
                    ),
                    {"snapshot_id": snapshot_id},
                ).mappings()
            ]
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
            review_contracts = [
                dict(row)
                for row in connection.execute(
                    text(
                        "select snapshot_id, review_kind, manifest_hash, review_hash, "
                        "reviewer, reviewed_at from public.backfill_review_contracts "
                        "where snapshot_id=:snapshot_id order by review_kind"
                    ),
                    {"snapshot_id": snapshot_id},
                ).mappings()
            ]
        return receipts, quarantine_count, seals, review_contracts
    finally:
        engine.dispose()


def _verify_source_receipts(
    *,
    source_rows: Mapping[str, list[Mapping[str, object]]],
    manifest_tables: tuple[_ManifestTable, ...],
    snapshot_id: str,
    actual_rows: list[Mapping[str, object]],
) -> tuple[VerificationIssue, ...]:
    expected_rows = reference_backfill_receipts(
        source_rows,
        primary_keys={table.table: table.primary_key for table in manifest_tables},
        snapshot_id=snapshot_id,
    )
    expected = {
        (row.source_table, row.source_primary_key): row for row in expected_rows
    }
    actual = {
        (str(row["source_table"]), str(row["source_primary_key"])): row
        for row in actual_rows
    }
    issues: list[VerificationIssue] = []
    for key in sorted(set(expected) - set(actual)):
        issues.append(
            VerificationIssue(
                "source_receipt_missing",
                f"source-receipt:{key[0]}:{key[1]}",
                "deterministic receipt identity is missing",
            )
        )
    for key in sorted(set(actual) - set(expected)):
        issues.append(
            VerificationIssue(
                "source_receipt_unexpected",
                f"source-receipt:{key[0]}:{key[1]}",
                "receipt identity has no immutable source row",
            )
        )
    for key in sorted(set(expected) & set(actual)):
        wanted = expected[key]
        found = actual[key]
        differences: list[str] = []
        if str(found["snapshot_id"]) != snapshot_id:
            differences.append("snapshot_id")
        if str(found["canonical_source_key"]) != wanted.canonical_source_key:
            differences.append("canonical_source_key")
        found_book = None if found["book_id"] is None else str(found["book_id"])
        wanted_book = None if wanted.book_id is None else str(wanted.book_id)
        if found_book != wanted_book:
            differences.append("book_id")
        if bytes(found["source_hash"]) != wanted.source_hash:
            differences.append("source_hash")
        found_target = (
            None
            if found["target_entity_id"] is None
            else str(found["target_entity_id"])
        )
        wanted_target = (
            None if wanted.target_entity_id is None else str(wanted.target_entity_id)
        )
        if found_target != wanted_target:
            differences.append("target_entity_id")
        if differences:
            issues.append(
                VerificationIssue(
                    "source_receipt_mismatch",
                    f"source-receipt:{key[0]}:{key[1]}",
                    f"source-derived fields differ: {','.join(differences)}",
                )
            )
    return tuple(sorted(set(issues)))


def _review_target_uuid(kind: str, *parts: str) -> UUID:
    root = UUID("3f021172-6aa9-5b36-9208-f238bc35c596")
    namespace = uuid5(root, kind)
    encoded = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))
    return uuid5(namespace, encoded)


def _verify_credit_card_review_evidence(
    *,
    review: CreditCardSemanticReview | None,
    manifest_hash: str,
    snapshot_id: str,
    contracts: list[Mapping[str, object]],
    target_rows: Mapping[str, list[Mapping[str, object]]],
) -> tuple[VerificationIssue, ...]:
    issues: list[VerificationIssue] = []
    if review is None:
        if contracts:
            issues.append(
                VerificationIssue(
                    "credit_card_review_unexpected",
                    f"snapshot:{snapshot_id}",
                    "target contains a credit-card review without verifier input",
                )
            )
        return tuple(issues)
    if len(contracts) != 1:
        issues.append(
            VerificationIssue(
                "credit_card_review_missing",
                f"snapshot:{snapshot_id}",
                f"expected one bound review contract, found {len(contracts)}",
            )
        )
    else:
        contract = contracts[0]
        expected = {
            "snapshot_id": snapshot_id,
            "review_kind": "credit_card_semantics_v1",
            "manifest_hash": bytes.fromhex(manifest_hash),
            "review_hash": bytes.fromhex(review.content_sha256),
            "reviewer": review.reviewer,
            "reviewed_at": review.reviewed_at,
        }
        different = [
            field
            for field, value in expected.items()
            if contract.get(field) != value
        ]
        if different:
            issues.append(
                VerificationIssue(
                    "credit_card_review_mismatch",
                    f"snapshot:{snapshot_id}",
                    f"bound review fields differ: {','.join(different)}",
                )
            )

    balances = {
        (
            str(row["book_id"]),
            str(row["account_id"]),
            str(row["asset_code"]),
        ): int(row["balance_units"])
        for row in target_rows.get("account_balances", [])
    }
    for expected in review.expected_balances:
        target_key = (
            str(_review_target_uuid("book", expected.book_id)),
            str(
                _review_target_uuid(
                    "account", expected.book_id, expected.source_account_id
                )
            ),
            expected.asset_code,
        )
        raw_units = balances.get(target_key)
        natural_units = 0 if raw_units is None else -raw_units
        if natural_units != expected.natural_units:
            issues.append(
                VerificationIssue(
                    "credit_card_review_balance_mismatch",
                    (
                        "credit-card-balance:"
                        f"{expected.book_id}:{expected.source_account_id}:"
                        f"{expected.asset_code}"
                    ),
                    (
                        f"reviewed natural units={expected.natural_units} "
                        f"target={natural_units}"
                    ),
                )
            )
    return tuple(sorted(set(issues)))


def verify_backfill(
    *,
    source_url: str,
    target_url: str,
    manifest_path: Path,
    output_path: Path | None = None,
    credit_card_review_path: Path | None = None,
    credit_card_review: CreditCardSemanticReview | None = None,
) -> VerificationReport:
    if _database_identity(source_url) == _database_identity(target_url):
        raise ValueError("source and target must be different databases")
    _, manifest_hash, snapshot_id, expected_revision, manifest_tables = _read_manifest(
        manifest_path
    )
    expected_counts = {table.table: table.row_count for table in manifest_tables}
    source_revision, source_counts, source_hashes, source_rows = _read_source_facts(
        source_url, manifest_tables
    )
    if credit_card_review_path is not None and credit_card_review is not None:
        raise ValueError("supply either a credit-card review path or object, not both")
    card_scope = credit_card_transaction_scope(source_rows)
    if card_scope and credit_card_review_path is None and credit_card_review is None:
        raise ValueError(
            "credit-card semantic review is required to verify this snapshot"
        )
    if (
        credit_card_review_path is not None or credit_card_review is not None
    ) and not card_scope:
        raise ValueError(
            "credit-card semantic review was supplied for a snapshot without cards"
        )
    if credit_card_review_path is not None:
        credit_card_review = read_credit_card_review(
            credit_card_review_path,
            manifest=read_manifest(manifest_path),
            rows_by_table=source_rows,
        )
    target_rows, target_report = _read_target_facts(target_url)
    issues = list(target_report.issues)
    if manifest_tables:
        issues.extend(
            verify_source_target_semantics(
                source_rows,
                target_rows,
                snapshot_id=snapshot_id,
                credit_card_review=credit_card_review,
            )
        )
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
        expected_target_count = source_counts.get(source_table)
        if expected_target_count is not None and credit_card_review is not None:
            neutralized = tuple(
                decision
                for decision in credit_card_review.transactions
                if decision.post_import_action == "exact_reversal"
            )
            if source_table == "transactions":
                expected_target_count += len(neutralized)
            elif source_table == "postings":
                expected_target_count += sum(
                    len(decision.postings) for decision in neutralized
                )
        if expected_target_count is not None and (
            expected_target_count != target_report.counts.get(target_table, 0)
        ):
            issues.append(
                VerificationIssue(
                    "source_target_count_mismatch",
                    f"mapping:{source_table}->{target_table}",
                    "source and target fact counts differ",
                )
            )

    receipt_rows, quarantine_count, seals, review_contracts = _read_backfill_controls(
        target_url, snapshot_id
    )
    receipts = Counter(str(row["source_table"]) for row in receipt_rows)
    receipt_count = len(receipt_rows)
    issues.extend(
        _verify_source_receipts(
            source_rows=source_rows,
            manifest_tables=manifest_tables,
            snapshot_id=snapshot_id,
            actual_rows=receipt_rows,
        )
    )
    issues.extend(
        _verify_credit_card_review_evidence(
            review=credit_card_review,
            manifest_hash=manifest_hash,
            snapshot_id=snapshot_id,
            contracts=review_contracts,
            target_rows=target_rows,
        )
    )
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
        credit_card_review_hash=(
            None
            if credit_card_review is None
            else credit_card_review.content_sha256
        ),
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
    credit_card_review_path: Path | None = None,
    credit_card_review: CreditCardSemanticReview | None = None,
) -> VerificationReport:
    return verify_backfill(
        source_url=source_url,
        target_url=target_url,
        manifest_path=manifest_path,
        output_path=output_path,
        credit_card_review_path=credit_card_review_path,
        credit_card_review=credit_card_review,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.tools.backfill_v1.verify",
        description="Independent SQL/reference verifier for one V1-to-V2 backfill",
    )
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--credit-card-review", type=Path)
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
            credit_card_review_path=args.credit_card_review,
        )
    except (FileExistsError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "verify", "verify_backfill", "verify_target"]
