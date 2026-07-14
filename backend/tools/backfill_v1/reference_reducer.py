from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Mapping, TypeAlias
from uuid import UUID


Primitive: TypeAlias = str | int | bool | None
JSONValue: TypeAlias = Primitive | list["JSONValue"] | dict[str, "JSONValue"]
Row: TypeAlias = Mapping[str, object]

HASH_DOMAIN_V1 = b"track-anywhere:v2:ledger-event-hash:sha256:v1"
ZERO_HASH = bytes(32)

_EVENT_SCHEMAS = frozenset(
    {
        ("JournalTransactionPosted", 1),
        ("JournalTransactionReversed", 1),
        ("FinancialExternalReferenceCorrected", 1),
        ("ReportingLinesAssigned", 1),
        ("ReportingLinesCleared", 1),
        ("InvestmentLotAcquired", 1),
        ("InvestmentLotDisposed", 1),
    }
)


@dataclass(frozen=True, order=True, slots=True)
class VerificationIssue:
    code: str
    scope: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "scope": self.scope}


@dataclass(frozen=True, slots=True)
class VerificationReport:
    counts: dict[str, int]
    book_terminal_hashes: dict[str, str]
    projection_hashes: dict[str, str]
    issues: tuple[VerificationIssue, ...]
    source_counts: dict[str, int] | None = None
    receipt_count: int | None = None
    quarantine_count: int | None = None
    manifest_hash: str | None = None
    snapshot_id: str | None = None

    @property
    def status(self) -> str:
        return "PASS" if not self.issues else "FAIL"

    @property
    def issue_codes(self) -> frozenset[str]:
        return frozenset(issue.code for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "book_terminal_hashes": dict(sorted(self.book_terminal_hashes.items())),
            "counts": dict(sorted(self.counts.items())),
            "issues": [issue.to_dict() for issue in self.issues],
            "projection_hashes": dict(sorted(self.projection_hashes.items())),
            "status": self.status,
        }
        if self.snapshot_id is not None:
            result["snapshot_id"] = self.snapshot_id
        if self.manifest_hash is not None:
            result["manifest_hash"] = self.manifest_hash
        if self.source_counts is not None:
            result["source_counts"] = dict(sorted(self.source_counts.items()))
        if self.receipt_count is not None:
            result["receipt_count"] = self.receipt_count
        if self.quarantine_count is not None:
            result["quarantine_count"] = self.quarantine_count
        return result


def _utc_microseconds(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    normalized = value.astimezone(UTC)
    return (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}"
        f".{normalized.microsecond:06d}Z"
    )


def _json_value(value: object) -> JSONValue:
    if value is None or type(value) in {str, int, bool}:
        return value  # type: ignore[return-value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _utc_microseconds(value)
    if isinstance(value, Decimal):
        integral = value.to_integral_value()
        return int(integral) if value == integral else format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def reference_event_hash(event: Row) -> bytes:
    previous_hash = event["previous_hash"]
    if not isinstance(previous_hash, bytes) or len(previous_hash) != 32:
        raise ValueError("event previous_hash must contain 32 bytes")
    envelope = {
        "event_id": str(event["event_id"]),
        "book_id": str(event["book_id"]),
        "book_position": int(event["book_position"]),
        "stream_type": str(event["stream_type"]),
        "stream_id": str(event["stream_id"]),
        "stream_version": int(event["stream_version"]),
        "event_type": str(event["event_type"]),
        "event_schema_version": int(event["event_schema_version"]),
        "command_id": str(event["command_id"]),
        "actor_subject_id": str(event["actor_subject_id"]),
        "correlation_id": str(event["correlation_id"]),
        "causation_event_id": (
            None
            if event.get("causation_event_id") is None
            else str(event["causation_event_id"])
        ),
        "effective_at": _utc_microseconds(event["effective_at"]),
        "previous_hash": previous_hash.hex(),
    }
    payload = event["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("event payload must be an object")
    return hashlib.sha256(
        HASH_DOMAIN_V1
        + b"\0"
        + canonical_json_bytes(envelope)
        + b"\0"
        + canonical_json_bytes(payload)
    ).digest()


def _row_hash(rows: list[Row]) -> str:
    normalized = [_json_value(row) for row in rows]
    normalized.sort(key=lambda row: canonical_json_bytes(row))
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def _identity(value: object) -> str:
    return str(value)


def _units(value: object) -> int:
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("unit value is not integral")
        return int(value)
    if type(value) is int:
        return value
    if (
        type(value) is str
        and value
        and (value.isdecimal() or (value.startswith("-") and value[1:].isdecimal()))
    ):
        return int(value)
    raise ValueError("unit value is not a canonical integer")


def _add(issues: list[VerificationIssue], code: str, scope: str, detail: str) -> None:
    issues.append(VerificationIssue(code=code, scope=scope, detail=detail))


def _event_scope(event: Row) -> str:
    return f"event:{event.get('event_id')}"


def _verify_events(
    rows: Mapping[str, list[Row]], issues: list[VerificationIssue]
) -> tuple[dict[str, str], dict[tuple[str, str], Row]]:
    events = rows.get("ledger_events", [])
    books = {_identity(row["book_id"]) for row in rows.get("books", [])}
    event_by_book_id = {
        (_identity(row["book_id"]), _identity(row["event_id"])): row for row in events
    }
    terminal: dict[str, str] = {}
    by_book: dict[str, list[Row]] = defaultdict(list)
    for event in events:
        by_book[_identity(event["book_id"])].append(event)
        if _identity(event["book_id"]) not in books:
            _add(issues, "cross_book_link", _event_scope(event), "Book is missing")

    heads = {_identity(row["book_id"]): row for row in rows.get("book_event_heads", [])}
    for book_id in sorted(books | set(by_book) | set(heads)):
        ordered = sorted(
            by_book.get(book_id, []), key=lambda row: int(row["book_position"])
        )
        previous = ZERO_HASH
        for expected_position, event in enumerate(ordered, start=1):
            scope = _event_scope(event)
            position = int(event["book_position"])
            if position != expected_position:
                _add(
                    issues,
                    "book_position_noncontiguous",
                    scope,
                    f"expected position {expected_position}, found {position}",
                )
            stored_previous = event.get("previous_hash")
            if stored_previous != previous:
                _add(
                    issues,
                    "previous_hash_mismatch",
                    scope,
                    "stored previous hash does not equal prior Book event hash",
                )
            try:
                computed = reference_event_hash(event)
            except (KeyError, TypeError, ValueError) as error:
                _add(issues, "event_hash_input_invalid", scope, str(error))
            else:
                if event.get("event_hash") != computed:
                    _add(
                        issues,
                        "event_hash_mismatch",
                        scope,
                        "stored event hash differs from independent SHA-256",
                    )
            event_hash = event.get("event_hash")
            if isinstance(event_hash, bytes) and len(event_hash) == 32:
                previous = event_hash
            else:
                previous = ZERO_HASH
            schema = (
                str(event.get("event_type")),
                int(event.get("event_schema_version", 0)),
            )
            if schema not in _EVENT_SCHEMAS:
                _add(
                    issues,
                    "unknown_event_schema",
                    scope,
                    f"unregistered schema {schema[0]}@{schema[1]}",
                )
            causation = event.get("causation_event_id")
            if causation is not None:
                matching = [
                    key for key in event_by_book_id if key[1] == _identity(causation)
                ]
                if matching and matching[0][0] != book_id:
                    _add(
                        issues,
                        "cross_book_link",
                        scope,
                        "causation event belongs to another Book",
                    )
                elif not matching:
                    _add(
                        issues,
                        "causation_event_missing",
                        scope,
                        "causation event is absent",
                    )

        terminal[book_id] = previous.hex()
        head = heads.get(book_id)
        expected_position = len(ordered)
        if head is None:
            _add(issues, "book_head_missing", f"book:{book_id}", "Book head is absent")
        elif (
            int(head["last_position"]) != expected_position
            or head.get("last_hash") != previous
        ):
            _add(
                issues,
                "book_head_mismatch",
                f"book:{book_id}",
                "Book head does not bind the terminal event",
            )

    by_stream: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    for event in events:
        by_stream[
            (
                _identity(event["book_id"]),
                str(event["stream_type"]),
                _identity(event["stream_id"]),
            )
        ].append(event)
    stream_heads = {
        (
            _identity(row["book_id"]),
            str(row["stream_type"]),
            _identity(row["stream_id"]),
        ): row
        for row in rows.get("event_stream_heads", [])
    }
    for key, stream_events in sorted(by_stream.items()):
        ordered = sorted(stream_events, key=lambda row: int(row["book_position"]))
        for expected_version, event in enumerate(ordered, start=1):
            if int(event["stream_version"]) != expected_version:
                _add(
                    issues,
                    "stream_version_noncontiguous",
                    _event_scope(event),
                    f"expected stream version {expected_version}",
                )
        terminal_event = ordered[-1]
        head = stream_heads.get(key)
        if head is None:
            _add(
                issues,
                "stream_head_missing",
                f"stream:{key[0]}:{key[1]}:{key[2]}",
                "stream head is absent",
            )
        elif (
            int(head["last_version"]) != int(terminal_event["stream_version"])
            or int(head["last_book_position"]) != int(terminal_event["book_position"])
            or _identity(head["last_event_id"]) != _identity(terminal_event["event_id"])
        ):
            _add(
                issues,
                "stream_head_mismatch",
                f"stream:{key[0]}:{key[1]}:{key[2]}",
                "stream head does not bind its terminal event",
            )
    for key in sorted(set(stream_heads) - set(by_stream)):
        _add(
            issues,
            "stream_head_orphan",
            f"stream:{key[0]}:{key[1]}:{key[2]}",
            "stream head has no events",
        )
    return terminal, event_by_book_id


def _verify_journal(
    rows: Mapping[str, list[Row]],
    event_by_book_id: Mapping[tuple[str, str], Row],
    issues: list[VerificationIssue],
) -> None:
    transactions = {
        (_identity(row["book_id"]), _identity(row["transaction_id"])): row
        for row in rows.get("journal_transactions", [])
    }
    postings_by_transaction: dict[tuple[str, str], list[Row]] = defaultdict(list)
    accounts = {
        (_identity(row["book_id"]), _identity(row["account_id"])): row
        for row in rows.get("accounts", [])
    }
    for posting in rows.get("journal_postings", []):
        key = (_identity(posting["book_id"]), _identity(posting["transaction_id"]))
        postings_by_transaction[key].append(posting)
        account_key = (_identity(posting["book_id"]), _identity(posting["account_id"]))
        account = accounts.get(account_key)
        if account is None or str(account.get("asset_code")) != str(
            posting.get("asset_code")
        ):
            _add(
                issues,
                "cross_book_link",
                f"posting:{posting.get('posting_id')}",
                "posting account is absent from the same Book/asset",
            )

    for key, actual_postings in postings_by_transaction.items():
        if key not in transactions:
            _add(
                issues,
                "posting_transaction_missing",
                f"transaction:{key[0]}:{key[1]}",
                "posting transaction is absent",
            )
        sums: dict[str, dict[str, int]] = defaultdict(lambda: {"debit": 0, "credit": 0})
        for posting in actual_postings:
            try:
                sums[str(posting["asset_code"])][str(posting["side"])] += _units(
                    posting["units"]
                )
            except (KeyError, ValueError):
                _add(
                    issues,
                    "posting_units_invalid",
                    f"posting:{posting.get('posting_id')}",
                    "posting units are not integral",
                )
        for asset_code, sides in sums.items():
            if sides["debit"] != sides["credit"]:
                _add(
                    issues,
                    "unbalanced_transaction",
                    f"transaction:{key[0]}:{key[1]}",
                    f"{asset_code} debits and credits differ",
                )

    reversal_targets: Counter[tuple[str, str]] = Counter()
    for event in rows.get("ledger_events", []):
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        event_type = str(event.get("event_type"))
        if event_type == "JournalTransactionPosted":
            transaction_id = payload.get("transaction_id")
            expected_postings = payload.get("postings")
        elif event_type == "JournalTransactionReversed":
            transaction_id = payload.get("reversal_transaction_id")
            expected_postings = payload.get("inverse_postings")
            original = payload.get("reverses_transaction_id")
            if original is not None:
                reversal_targets[
                    (_identity(event["book_id"]), _identity(original))
                ] += 1
        else:
            continue
        book_id = _identity(event["book_id"])
        key = (book_id, _identity(transaction_id))
        scope = f"transaction:{book_id}:{transaction_id}"
        transaction = transactions.get(key)
        if transaction is None:
            _add(
                issues, "journal_transaction_missing", scope, "projection row is absent"
            )
            continue
        if _identity(transaction.get("source_event_id")) != _identity(
            event["event_id"]
        ):
            _add(
                issues,
                "transaction_source_mismatch",
                scope,
                "transaction points at a different event",
            )
        if transaction.get("effective_at") != event.get("effective_at"):
            _add(
                issues,
                "effective_time_mismatch",
                scope,
                "projected and event effective times differ",
            )
        if not isinstance(expected_postings, list):
            _add(
                issues, "event_payload_invalid", _event_scope(event), "postings missing"
            )
            continue
        actual = {
            _identity(posting["posting_id"]): posting
            for posting in postings_by_transaction.get(key, [])
        }
        expected_ids: set[str] = set()
        for expected in expected_postings:
            if not isinstance(expected, Mapping):
                _add(
                    issues,
                    "event_payload_invalid",
                    _event_scope(event),
                    "posting is not an object",
                )
                continue
            posting_id = _identity(expected.get("posting_id"))
            expected_ids.add(posting_id)
            persisted = actual.get(posting_id)
            if persisted is None:
                _add(
                    issues,
                    "lost_posting",
                    f"posting:{posting_id}",
                    "event posting is absent from the journal projection",
                )
                continue
            comparisons = (
                ("posting_position", "position", "posting_position_mismatch"),
                ("account_id", "account_id", "posting_account_mismatch"),
                ("asset_code", "asset_code", "posting_asset_mismatch"),
                ("side", "side", "posting_side_mismatch"),
            )
            for actual_name, expected_name, code in comparisons:
                if _identity(persisted.get(actual_name)) != _identity(
                    expected.get(expected_name)
                ):
                    _add(
                        issues,
                        code,
                        f"posting:{posting_id}",
                        f"{actual_name} differs from immutable event payload",
                    )
            try:
                units_match = _units(persisted.get("units")) == _units(
                    expected.get("units")
                )
            except ValueError:
                units_match = False
            if not units_match:
                code = (
                    "usdt_unit_mismatch"
                    if str(expected.get("asset_code")) == "USDT"
                    else "posting_units_mismatch"
                )
                _add(
                    issues,
                    code,
                    f"posting:{posting_id}",
                    "projected units differ from immutable event payload",
                )
        for unexpected_id in sorted(set(actual) - expected_ids):
            _add(
                issues,
                "unexpected_posting",
                f"posting:{unexpected_id}",
                "projection posting is absent from the immutable event payload",
            )

    for (book_id, original_id), count in sorted(reversal_targets.items()):
        if count > 1:
            _add(
                issues,
                "duplicate_reversal",
                f"transaction:{book_id}:{original_id}",
                f"{count} reversal events target one transaction",
            )

    reversal_rows: Counter[tuple[str, str]] = Counter()
    for reversal in rows.get("transaction_reversals", []):
        key = (
            _identity(reversal["book_id"]),
            _identity(reversal["original_transaction_id"]),
        )
        reversal_rows[key] += 1
        for event_column in ("source_event_id", "original_event_id"):
            if (key[0], _identity(reversal[event_column])) not in event_by_book_id:
                _add(
                    issues,
                    "cross_book_link",
                    f"reversal:{reversal.get('reversal_transaction_id')}",
                    f"{event_column} is absent from the same Book",
                )
    for key, count in reversal_rows.items():
        if count > 1:
            _add(
                issues,
                "duplicate_reversal",
                f"transaction:{key[0]}:{key[1]}",
                "multiple reversal projection rows target one transaction",
            )

    expected_balances: Counter[tuple[str, str, str]] = Counter()
    for posting in rows.get("journal_postings", []):
        try:
            signed = _units(posting["units"])
        except (KeyError, ValueError):
            continue
        if str(posting.get("side")) == "credit":
            signed = -signed
        expected_balances[
            (
                _identity(posting["book_id"]),
                _identity(posting["account_id"]),
                str(posting["asset_code"]),
            )
        ] += signed
    actual_balances = {
        (
            _identity(row["book_id"]),
            _identity(row["account_id"]),
            str(row["asset_code"]),
        ): row
        for row in rows.get("account_balances", [])
    }
    for key in sorted(set(expected_balances) | set(actual_balances)):
        row = actual_balances.get(key)
        try:
            actual_units = 0 if row is None else _units(row["balance_units"])
        except ValueError:
            actual_units = 0
        if actual_units != expected_balances[key]:
            _add(
                issues,
                "balance_projection_mismatch",
                f"account:{key[0]}:{key[1]}:{key[2]}",
                "balance does not equal the independent posting reduction",
            )


def _verify_reporting(
    rows: Mapping[str, list[Row]], issues: list[VerificationIssue]
) -> None:
    lines = rows.get("reporting_lines", [])
    by_revision: dict[tuple[str, str, int], list[Row]] = defaultdict(list)
    categories_by_book = {
        (_identity(row["book_id"]), _identity(row["category_id"]))
        for row in rows.get("categories", [])
    }
    all_category_ids = {category_id for _, category_id in categories_by_book}
    for line in lines:
        key = (
            _identity(line["book_id"]),
            _identity(line["transaction_id"]),
            int(line["classification_revision"]),
        )
        by_revision[key].append(line)
        if (
            str(line.get("dimension")) == "category"
            and line.get("dimension_id") is not None
        ):
            dimension_id = _identity(line["dimension_id"])
            if (key[0], dimension_id) not in categories_by_book:
                code = (
                    "cross_book_link"
                    if dimension_id in all_category_ids
                    else "classification_reference_missing"
                )
                _add(
                    issues,
                    code,
                    f"reporting-line:{line.get('line_id')}",
                    "classification dimension is absent from the same Book",
                )

    for event in rows.get("ledger_events", []):
        if str(event.get("event_type")) != "ReportingLinesAssigned":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        try:
            key = (
                _identity(event["book_id"]),
                _identity(payload["transaction_id"]),
                int(payload["classification_revision"]),
            )
        except (KeyError, TypeError, ValueError):
            _add(
                issues,
                "event_payload_invalid",
                _event_scope(event),
                "classification identity invalid",
            )
            continue
        expected_lines = payload.get("lines")
        if not isinstance(expected_lines, list):
            _add(
                issues,
                "event_payload_invalid",
                _event_scope(event),
                "classification lines missing",
            )
            continue
        actual = {_identity(line["line_id"]): line for line in by_revision.get(key, [])}
        expected_ids: set[str] = set()
        for expected in expected_lines:
            if not isinstance(expected, Mapping):
                continue
            line_id = _identity(expected.get("line_id"))
            expected_ids.add(line_id)
            persisted = actual.get(line_id)
            if persisted is None:
                _add(
                    issues,
                    "classification_mismatch",
                    f"reporting-line:{line_id}",
                    "event classification line is missing",
                )
                continue
            fields = (
                ("line_version_id", "line_version_id"),
                ("catalog_id", "catalog_id"),
                ("line_position", "position"),
                ("asset_code", "asset_code"),
                ("units", "units"),
                ("line_kind", "line_kind"),
                ("dimension", "dimension"),
                ("dimension_id", "dimension_id"),
                ("description_ref", "description_ref"),
            )
            for actual_name, expected_name in fields:
                actual_value = persisted.get(actual_name)
                expected_value = expected.get(expected_name)
                if actual_name == "units":
                    try:
                        equal = _units(actual_value) == _units(expected_value)
                    except ValueError:
                        equal = False
                else:
                    equal = _identity(actual_value) == _identity(expected_value)
                if not equal:
                    _add(
                        issues,
                        "classification_mismatch",
                        f"reporting-line:{line_id}",
                        f"{actual_name} differs from immutable event payload",
                    )
        for line_id in sorted(set(actual) - expected_ids):
            _add(
                issues,
                "classification_mismatch",
                f"reporting-line:{line_id}",
                "projection classification line is absent from the event payload",
            )


def reduce_target(rows_by_table: Mapping[str, list[Row]]) -> VerificationReport:
    rows = {table: list(records) for table, records in rows_by_table.items()}
    issues: list[VerificationIssue] = []
    terminal, event_by_book_id = _verify_events(rows, issues)
    _verify_journal(rows, event_by_book_id, issues)
    _verify_reporting(rows, issues)
    counts = {table: len(records) for table, records in sorted(rows.items())}
    projection_groups = {
        "journal": (
            "journal_transactions",
            "journal_postings",
            "account_balances",
            "transaction_reversals",
        ),
        "reporting": ("reporting_lines",),
        "investments": ("investment_lots", "investment_lot_allocations"),
    }
    projection_hashes = {
        name: hashlib.sha256(
            canonical_json_bytes(
                {table: [dict(row) for row in rows.get(table, [])] for table in tables}
            )
        ).hexdigest()
        for name, tables in projection_groups.items()
    }
    # Include a full event hash so deterministic runs compare more than terminal heads.
    projection_hashes["events"] = _row_hash(
        [
            {
                key: value
                for key, value in event.items()
                if key not in {"global_sequence", "recorded_at"}
            }
            for event in rows.get("ledger_events", [])
        ]
    )
    return VerificationReport(
        counts=counts,
        book_terminal_hashes=dict(sorted(terminal.items())),
        projection_hashes=dict(sorted(projection_hashes.items())),
        issues=tuple(sorted(set(issues))),
    )


__all__ = [
    "HASH_DOMAIN_V1",
    "VerificationIssue",
    "VerificationReport",
    "canonical_json_bytes",
    "reduce_target",
    "reference_event_hash",
]
