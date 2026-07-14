from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping, TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid5


Primitive: TypeAlias = str | int | bool | None
JSONValue: TypeAlias = Primitive | list["JSONValue"] | dict[str, "JSONValue"]
Row: TypeAlias = Mapping[str, object]

HASH_DOMAIN_V1 = b"track-anywhere:v2:ledger-event-hash:sha256:v1"
ZERO_HASH = bytes(32)

# These are copied protocol constants, deliberately not imported from the
# backfill implementation or application handlers.  A change on either side
# therefore becomes observable instead of changing the verifier in lockstep.
_BACKFILL_V1_NAMESPACE = UUID("3f021172-6aa9-5b36-9208-f238bc35c596")
_PLAIN_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SOURCE_ACTOR_HASH_DOMAIN_V1 = b"track-anywhere:v2:backfill:source-actor:v1\x00"
_EVENT_NAMESPACES = {
    "journal.post": uuid5(
        NAMESPACE_URL, "https://track-anywhere.dev/v2/events/journal.post"
    ),
    "journal.reverse": uuid5(
        NAMESPACE_URL, "https://track-anywhere.dev/v2/events/journal.reverse"
    ),
    "reporting.assign": uuid5(
        NAMESPACE_URL, "https://track-anywhere.dev/v2/events/reporting.assign"
    ),
}

_EVENT_SCHEMAS = frozenset(
    {
        ("JournalTransactionPosted", 1),
        ("JournalTransactionReversed", 1),
        ("FinancialExternalReferenceCorrected", 1),
        ("ReportingLinesAssigned", 1),
        ("ReportingLinesCleared", 1),
        ("InvestmentLotAcquired", 1),
        ("InvestmentLotDisposed", 1),
        ("HistoricalCategoryActivityImported", 1),
        ("HistoricalInvestmentActivityImported", 1),
        ("HistoricalReportingLineImported", 1),
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

    latest_events: dict[tuple[str, str], tuple[int, Row]] = {}
    for event in rows.get("ledger_events", []):
        if str(event.get("event_type")) != "ReportingLinesAssigned":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        try:
            revision = int(payload["classification_revision"])
            target = (
                _identity(event["book_id"]),
                _identity(payload["transaction_id"]),
            )
        except (KeyError, TypeError, ValueError):
            _add(
                issues,
                "event_payload_invalid",
                _event_scope(event),
                "classification identity invalid",
            )
            continue
        previous = latest_events.get(target)
        if previous is None or revision > previous[0]:
            latest_events[target] = (revision, event)

    for (book_id, transaction_id), (revision, event) in sorted(latest_events.items()):
        payload = event["payload"]
        if not isinstance(payload, Mapping):
            continue
        key = (book_id, transaction_id, revision)
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


def _deterministic_uuid(kind: str, *source_parts: str) -> UUID:
    if not source_parts or any(
        type(part) is not str or not part for part in source_parts
    ):
        raise ValueError("deterministic UUID source parts must be nonblank strings")
    kind_namespace = uuid5(_BACKFILL_V1_NAMESPACE, kind)
    encoded = json.dumps(list(source_parts), ensure_ascii=False, separators=(",", ":"))
    return uuid5(kind_namespace, encoded)


def _source_time(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("source timestamp is not ISO-8601") from None
    else:
        raise ValueError("source timestamp has an invalid type")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source timestamp must include an offset")
    return parsed.astimezone(UTC)


def _source_units(value: object, *, scale: int) -> int:
    amount = str(value)
    if _PLAIN_DECIMAL.fullmatch(amount) is None:
        raise ValueError("source amount must be a plain decimal")
    try:
        parsed = Decimal(amount)
    except InvalidOperation:
        raise ValueError("source amount is invalid") from None
    if not parsed.is_finite():
        raise ValueError("source amount must be finite")
    scaled = parsed * (Decimal(10) ** scale)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError("source amount is not representable at ledger scale")
    units = int(integral)
    if len(str(abs(units))) > 48:
        raise ValueError("source amount exceeds the unit bound")
    return units


def _source_canonical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite source decimal")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite source float")
        return format(Decimal(repr(value)), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive source datetime")
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
            str(key): _source_canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_source_canonical_value(item) for item in value]
    raise TypeError(f"unsupported source value: {type(value).__name__}")


def _source_object_hash(value: Mapping[str, object]) -> str:
    canonical = {
        str(key): _source_canonical_value(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
    return hashlib.sha256(canonical_json_bytes(canonical)).hexdigest()


def _source_actor_hash(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("source actor must be nonblank")
    return hashlib.sha256(
        _SOURCE_ACTOR_HASH_DOMAIN_V1 + value.encode("utf-8")
    ).hexdigest()


def _source_decimal_payload(value: object) -> dict[str, object]:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("source decimal is invalid") from None
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("source decimal must be positive")
    sign, digits, exponent = parsed.as_tuple()
    if sign:
        raise ValueError("source decimal must be unsigned")
    unscaled = "".join(str(digit) for digit in digits) or "0"
    if exponent >= 0:
        unscaled += "0" * exponent
        scale = 0
    else:
        scale = -exponent
    unscaled = str(int(unscaled))
    if scale > 30 or len(unscaled) > 38:
        raise ValueError("source decimal exceeds typed history bounds")
    return {"unscaled_units": unscaled, "scale": scale}


def _historical_reporting_kind(line: Row) -> str | None:
    if (
        line.get("category_id") is not None
        or line.get("category_version_id") is not None
    ):
        return None
    line_kind = str(line.get("line_type"))
    return line_kind if line_kind in {"fx_exchange", "fx_fee"} else None


def _source_fx_shape(
    source: Mapping[str, list[Row]],
) -> tuple[set[str], set[tuple[str, str]]]:
    accounts = {
        (str(row["book_id"]), str(row["account_id"])): row
        for row in source.get("accounts", [])
    }
    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    lines_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for row in source.get("postings", []):
        postings_by_transaction[str(row["transaction_id"])].append(row)
    for row in source.get("transaction_lines", []):
        lines_by_transaction[str(row["transaction_id"])].append(row)

    pure_transactions: set[str] = set()
    trading_accounts: set[tuple[str, str]] = set()
    trading_account_by_asset: dict[tuple[str, str], str] = {}
    for transaction in source.get("transactions", []):
        transaction_id = str(transaction["transaction_id"])
        source_book = str(transaction["book_id"])
        postings = postings_by_transaction[transaction_id]
        if (
            transaction.get("reverses_transaction_id") is not None
            or len(postings) != 4
            or not any(
                _historical_reporting_kind(line) == "fx_exchange"
                for line in lines_by_transaction[transaction_id]
            )
        ):
            continue
        by_asset: dict[str, list[Row]] = defaultdict(list)
        for posting in postings:
            by_asset[str(posting["currency"])].append(posting)
        if len(by_asset) != 2 or any(len(values) != 2 for values in by_asset.values()):
            continue
        trading_rows: list[Row] = []
        valid = True
        for asset_code, asset_postings in by_asset.items():
            system_postings = [
                posting
                for posting in asset_postings
                if str(accounts[(source_book, str(posting["account_id"]))]["type"])
                == "system"
            ]
            if len(system_postings) != 1:
                valid = False
                break
            system_posting = system_postings[0]
            user_posting = next(
                posting for posting in asset_postings if posting is not system_posting
            )
            system_account = accounts[(source_book, str(system_posting["account_id"]))]
            if (
                system_posting.get("side") == user_posting.get("side")
                or str(system_account["currency"]) != asset_code
            ):
                valid = False
                break
            trading_rows.append(system_account)
        trading_ids = {str(row["account_id"]) for row in trading_rows}
        trading_sides = {
            str(posting["side"])
            for posting in postings
            if str(posting["account_id"]) in trading_ids
        }
        if not valid or trading_sides != {"debit", "credit"}:
            continue
        pure_transactions.add(transaction_id)
        for account in trading_rows:
            account_id = str(account["account_id"])
            asset_key = (source_book, str(account["currency"]))
            existing = trading_account_by_asset.setdefault(asset_key, account_id)
            if existing != account_id:
                raise ValueError(
                    "multiple source FX trading accounts share a Book asset"
                )
            trading_accounts.add((source_book, account_id))
    return pure_transactions, trading_accounts


def _source_kind(value: object) -> str:
    normalized = str(value or "").casefold()
    if "opening" in normalized:
        return "opening"
    if "transfer" in normalized:
        return "transfer"
    if "adjust" in normalized:
        return "adjustment"
    return "standard"


def _source_line_kind(value: object) -> str:
    normalized = str(value or "").casefold()
    if "income" in normalized or "dividend" in normalized:
        return "income"
    if "transfer" in normalized:
        return "transfer"
    if "tax" in normalized:
        return "tax"
    if "invest" in normalized or normalized in {"buy", "sell"}:
        return "investment"
    return "expense"


def _expected_posting(
    row: Row,
    *,
    transaction: Row,
    scales: Mapping[str, int],
    position: int,
) -> dict[str, object]:
    source_book_id = str(transaction["book_id"])
    source_transaction_id = str(row["transaction_id"])
    asset_code = str(row["currency"])
    scale = scales[asset_code]
    signed_units = _source_units(row.get("amount"), scale=scale)
    semantics = str(row.get("amount_semantics"))
    if semantics == "legacy_signed":
        if signed_units == 0:
            raise ValueError("legacy signed posting is zero")
        side = "debit" if signed_units > 0 else "credit"
        units = abs(signed_units)
    elif semantics == "debit_credit":
        side = str(row.get("side"))
        if side not in {"debit", "credit"} or signed_units <= 0:
            raise ValueError("debit/credit posting semantics are invalid")
        units = signed_units
    else:
        raise ValueError("posting amount semantics are unknown")
    return {
        "posting_id": _deterministic_uuid(
            "posting", source_book_id, source_transaction_id, str(row["id"])
        ),
        "posting_position": position,
        "account_id": _deterministic_uuid(
            "account", source_book_id, str(row["account_id"])
        ),
        "asset_code": asset_code,
        "side": side,
        "units": units,
    }


def _category_source_for_line(
    line: Row, category_versions: Mapping[str, Row]
) -> str | None:
    if line.get("category_id") is not None:
        return str(line["category_id"])
    version_id = line.get("category_version_id")
    if version_id is None:
        return None
    version = category_versions.get(str(version_id))
    return None if version is None else str(version["category_id"])


def _classification_state(value: Row) -> tuple[object, object, object]:
    return (
        value.get("category_id"),
        value.get("category_version_id"),
        _source_canonical_value(value.get("category_path_snapshot")),
    )


def _exact_value(value: object) -> object:
    return _json_value(value)


def _compare_expected_rows(
    *,
    expected: Mapping[tuple[str, ...], Mapping[str, object]],
    actual_rows: list[Row],
    key_fields: tuple[str, ...],
    compared_fields: tuple[str, ...],
    scope_name: str,
    missing_code: str,
    unexpected_code: str,
    mismatch_code: str,
    issues: list[VerificationIssue],
) -> None:
    actual = {
        tuple(_identity(row[field]) for field in key_fields): row for row in actual_rows
    }
    for key in sorted(set(expected) - set(actual)):
        _add(
            issues,
            missing_code,
            f"{scope_name}:{':'.join(key)}",
            "deterministic target row is missing",
        )
    for key in sorted(set(actual) - set(expected)):
        _add(
            issues,
            unexpected_code,
            f"{scope_name}:{':'.join(key)}",
            "target row has no source-derived identity",
        )
    for key in sorted(set(expected) & set(actual)):
        wanted = expected[key]
        found = actual[key]
        different = [
            field
            for field in compared_fields
            if _exact_value(found.get(field)) != _exact_value(wanted.get(field))
        ]
        if different:
            _add(
                issues,
                mismatch_code,
                f"{scope_name}:{':'.join(key)}",
                f"source-derived fields differ: {','.join(different)}",
            )


@dataclass(frozen=True)
class _SourceScheduleAction:
    identity: tuple[str, str, str]
    kind: str
    row: Row
    base_key: tuple[bytes, datetime, bytes, int]


def _source_action_identity(kind: str, row: Row) -> tuple[str, str, str]:
    source_id_field = {
        "transaction": "transaction_id",
        "classification": "classification_event_id",
        "investment": "event_id",
    }[kind]
    return kind, str(row["book_id"]), str(row[source_id_field])


def _source_canonical_schedule(
    source: Mapping[str, list[Row]],
) -> tuple[_SourceScheduleAction, ...]:
    """Independently reproduce the frozen-source action DAG and stable Kahn order."""

    kind_specs = (
        ("transaction", "transactions", "transaction_id", "occurred_at", 0),
        (
            "classification",
            "classification_events",
            "classification_event_id",
            "created_at",
            1,
        ),
        ("investment", "investment_events", "event_id", "occurred_at", 2),
    )
    actions: dict[tuple[str, str, str], _SourceScheduleAction] = {}
    dependencies: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {}
    for kind, table, source_id_field, time_field, rank in kind_specs:
        for row in source.get(table, []):
            identity = _source_action_identity(kind, row)
            if identity in actions:
                raise ValueError(f"duplicate source schedule identity {identity}")
            source_id = str(row[source_id_field])
            action = _SourceScheduleAction(
                identity=identity,
                kind=kind,
                row=row,
                base_key=(
                    _deterministic_uuid("book", str(row["book_id"])).bytes,
                    _source_time(row.get(time_field)),
                    source_id.encode("utf-8"),
                    rank,
                ),
            )
            actions[identity] = action
            dependencies[identity] = set()

    transactions = {
        (str(row["book_id"]), str(row["transaction_id"])): row
        for row in source.get("transactions", [])
    }
    for (source_book, source_transaction_id), row in transactions.items():
        original_source_id = row.get("reverses_transaction_id")
        if original_source_id is None:
            continue
        child = ("transaction", source_book, source_transaction_id)
        parent = ("transaction", source_book, str(original_source_id))
        parent_action = actions.get(parent)
        if parent_action is None:
            raise ValueError("source reversal dependency is missing")
        if actions[child].base_key[1] < parent_action.base_key[1]:
            raise ValueError("source reversal precedes its original transaction")
        dependencies[child].add(parent)

    reclassifications_by_line: dict[
        tuple[str, str, str], list[_SourceScheduleAction]
    ] = defaultdict(list)
    for row in source.get("classification_events", []):
        if str(row.get("event_type")) != "reclassify":
            continue
        after = row.get("after")
        if not isinstance(after, Mapping):
            raise ValueError("source reclassification after-state is not an object")
        source_book = str(row["book_id"])
        source_transaction_id = str(after.get("transaction_id"))
        source_line_id = str(after.get("line_id"))
        child = _source_action_identity("classification", row)
        parent = ("transaction", source_book, source_transaction_id)
        parent_action = actions.get(parent)
        if parent_action is None:
            raise ValueError("source reclassification target transaction is missing")
        if actions[child].base_key[1] < parent_action.base_key[1]:
            raise ValueError("source reclassification precedes its target transaction")
        dependencies[child].add(parent)
        reclassifications_by_line[
            (source_book, source_transaction_id, source_line_id)
        ].append(actions[child])

    for line_actions in reclassifications_by_line.values():
        ordered_line_actions = sorted(
            line_actions,
            key=lambda action: (action.base_key[1], action.base_key[2]),
        )
        for previous, current in zip(
            ordered_line_actions, ordered_line_actions[1:], strict=False
        ):
            dependencies[current.identity].add(previous.identity)

    pending = set(actions)
    emitted: set[tuple[str, str, str]] = set()
    ordered: list[_SourceScheduleAction] = []
    while pending:
        ready = [
            identity for identity in pending if dependencies[identity].issubset(emitted)
        ]
        if not ready:
            raise ValueError("source schedule dependency graph is cyclic")
        selected = min(ready, key=lambda identity: actions[identity].base_key)
        ordered.append(actions[selected])
        emitted.add(selected)
        pending.remove(selected)
    return tuple(ordered)


def _expected_event(
    *,
    event_id: UUID,
    book_id: UUID,
    book_position: int,
    stream_type: str,
    stream_id: UUID,
    event_type: str,
    command_id: UUID,
    actor_subject_id: str,
    causation_event_id: UUID | None,
    effective_at: datetime,
    payload: Mapping[str, object],
    previous_hash: bytes,
    stream_version: int = 1,
) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": event_id,
        "book_id": book_id,
        "book_position": book_position,
        "stream_type": stream_type,
        "stream_id": stream_id,
        "stream_version": stream_version,
        "event_type": event_type,
        "event_schema_version": 1,
        "command_id": command_id,
        "actor_subject_id": actor_subject_id,
        "correlation_id": command_id,
        "causation_event_id": causation_event_id,
        "effective_at": effective_at,
        "payload": dict(payload),
        "previous_hash": previous_hash,
    }
    event["event_hash"] = reference_event_hash(event)
    return event


def verify_source_target_semantics(
    source_rows_by_table: Mapping[str, list[Row]],
    target_rows_by_table: Mapping[str, list[Row]],
    *,
    snapshot_id: str,
) -> tuple[VerificationIssue, ...]:
    """Map the frozen V1 rows independently and compare exact V2 facts/events."""

    source = {table: list(rows) for table, rows in source_rows_by_table.items()}
    target = {table: list(rows) for table, rows in target_rows_by_table.items()}
    issues: list[VerificationIssue] = []
    scales = {
        str(row["asset_code"]): int(row["scale"]) for row in source.get("assets", [])
    }
    try:
        pure_fx_transactions, fx_trading_accounts = _source_fx_shape(source)
    except (KeyError, ValueError) as error:
        _add(
            issues,
            "source_fx_semantics_invalid",
            "source-table:transactions",
            str(error),
        )
        pure_fx_transactions, fx_trading_accounts = set(), set()

    expected_books: dict[tuple[str, ...], Mapping[str, object]] = {}
    for row in source.get("ledger_books", []):
        book_id = _deterministic_uuid("book", str(row["book_id"]))
        expected_books[(str(book_id),)] = {
            "book_id": book_id,
            "current_name": str(row["name"]).strip(),
            "base_asset_code": str(row["base_currency"]),
            "write_state": "active",
        }
    _compare_expected_rows(
        expected=expected_books,
        actual_rows=target.get("books", []),
        key_fields=("book_id",),
        compared_fields=("current_name", "base_asset_code", "write_state"),
        scope_name="source-book",
        missing_code="source_catalog_missing",
        unexpected_code="source_catalog_unexpected",
        mismatch_code="source_catalog_mismatch",
        issues=issues,
    )

    expected_assets = {
        (str(row["asset_code"]),): {
            "asset_code": str(row["asset_code"]),
            "kind": str(row["kind"]).strip(),
            "ledger_scale": int(row["scale"]),
            "input_scale": (
                min(6, int(row["scale"]))
                if str(row["asset_code"]) == "USDT"
                else int(row["scale"])
            ),
            "display_scale": int(row["display_scale"]),
            "current_name": str(row["name"]).strip(),
            "status": (
                "active" if str(row.get("status", "active")) == "active" else "disabled"
            ),
        }
        for row in source.get("assets", [])
    }
    _compare_expected_rows(
        expected=expected_assets,
        actual_rows=target.get("assets", []),
        key_fields=("asset_code",),
        compared_fields=(
            "kind",
            "ledger_scale",
            "input_scale",
            "display_scale",
            "current_name",
            "status",
        ),
        scope_name="source-asset",
        missing_code="source_catalog_missing",
        unexpected_code="source_catalog_unexpected",
        mismatch_code="source_catalog_mismatch",
        issues=issues,
    )

    expected_accounts: dict[tuple[str, ...], Mapping[str, object]] = {}
    for row in source.get("accounts", []):
        book_id = _deterministic_uuid("book", str(row["book_id"]))
        account_id = _deterministic_uuid(
            "account", str(row["book_id"]), str(row["account_id"])
        )
        expected_accounts[(str(book_id), str(account_id))] = {
            "book_id": book_id,
            "account_id": account_id,
            "asset_code": str(row["currency"]),
            "account_type": str(row["type"]).strip(),
            "system_role": (
                "fx_trading"
                if (str(row["book_id"]), str(row["account_id"])) in fx_trading_accounts
                else None
            ),
            "current_name": str(row["name"]).strip(),
            "status": "active",
        }
    _compare_expected_rows(
        expected=expected_accounts,
        actual_rows=target.get("accounts", []),
        key_fields=("book_id", "account_id"),
        compared_fields=(
            "asset_code",
            "account_type",
            "system_role",
            "current_name",
            "status",
        ),
        scope_name="source-account",
        missing_code="source_catalog_missing",
        unexpected_code="source_catalog_unexpected",
        mismatch_code="source_catalog_mismatch",
        issues=issues,
    )

    source_categories = {
        str(row["category_id"]): row for row in source.get("categories", [])
    }
    versions_by_category: dict[str, list[Row]] = defaultdict(list)
    source_category_versions: dict[str, Row] = {}
    for row in source.get("category_versions", []):
        versions_by_category[str(row["category_id"])].append(row)
        source_category_versions[str(row["category_version_id"])] = row

    def current_version(category: Row) -> Row | None:
        versions = versions_by_category.get(str(category["category_id"]), [])
        if not versions:
            return None
        active = [version for version in versions if version.get("valid_to") is None]
        return max(
            active or versions,
            key=lambda version: (
                str(version.get("valid_from", "")),
                str(version["category_version_id"]),
            ),
        )

    expected_categories: dict[tuple[str, ...], Mapping[str, object]] = {}
    expected_versions: dict[tuple[str, ...], Mapping[str, object]] = {}
    for category in source.get("categories", []):
        source_book = str(category["book_id"])
        source_category = str(category["category_id"])
        book_id = _deterministic_uuid("book", source_book)
        category_id = _deterministic_uuid("category", source_book, source_category)
        chosen = current_version(category)
        chosen_source_version = (
            f"synthetic:{source_category}"
            if chosen is None
            else str(chosen["category_version_id"])
        )
        current_version_id = _deterministic_uuid(
            "category_version", source_book, chosen_source_version
        )
        parent = (
            category.get("parent_id") if chosen is None else chosen.get("parent_id")
        )
        name = category.get("name") if chosen is None else chosen.get("name")
        parent_id = (
            None
            if parent is None
            else _deterministic_uuid("category", source_book, str(parent))
        )
        expected_categories[(str(book_id), str(category_id))] = {
            "book_id": book_id,
            "category_id": category_id,
            "parent_category_id": parent_id,
            "current_name": str(name),
            "current_version_id": current_version_id,
            "status": (
                "archived" if str(category.get("status")) == "archived" else "active"
            ),
        }
        if chosen is None:
            expected_versions[
                (str(book_id), str(category_id), str(current_version_id))
            ] = {
                "book_id": book_id,
                "category_id": category_id,
                "category_version_id": current_version_id,
                "parent_category_id": parent_id,
                "name": str(name),
                "status": "active",
                "change_reason_code": "backfill_current",
            }
    for version in source.get("category_versions", []):
        source_book = str(version["book_id"])
        source_category = str(version["category_id"])
        book_id = _deterministic_uuid("book", source_book)
        category_id = _deterministic_uuid("category", source_book, source_category)
        version_id = _deterministic_uuid(
            "category_version", source_book, str(version["category_version_id"])
        )
        parent = version.get("parent_id")
        expected_versions[(str(book_id), str(category_id), str(version_id))] = {
            "book_id": book_id,
            "category_id": category_id,
            "category_version_id": version_id,
            "parent_category_id": (
                None
                if parent is None
                else _deterministic_uuid("category", source_book, str(parent))
            ),
            "name": str(version["name"]),
            "status": (
                "archived"
                if version.get("valid_to") is not None
                or str(source_categories[source_category].get("status")) == "archived"
                else "active"
            ),
            "change_reason_code": str(version.get("change_reason") or "backfill")[:64],
        }

    postings_by_transaction: dict[str, list[Row]] = defaultdict(list)
    lines_by_transaction: dict[str, list[Row]] = defaultdict(list)
    for row in source.get("postings", []):
        postings_by_transaction[str(row["transaction_id"])].append(row)
    for row in source.get("transaction_lines", []):
        lines_by_transaction[str(row["transaction_id"])].append(row)
        if _category_source_for_line(row, source_category_versions) is None:
            if _historical_reporting_kind(row) is not None:
                continue
            _add(
                issues,
                "source_reporting_dimension_unmapped",
                f"source-line:{row.get('line_id')}",
                "V1 line has no category identity that can be proven in V2",
            )
            continue
        if row.get("category_version_id") is None:
            source_book = str(row["book_id"])
            source_category = _category_source_for_line(row, source_category_versions)
            if source_category is None or source_category not in source_categories:
                continue
            source_line = str(row["line_id"])
            book_id = _deterministic_uuid("book", source_book)
            category_id = _deterministic_uuid("category", source_book, source_category)
            version_id = _deterministic_uuid(
                "category_version", source_book, f"line-snapshot:{source_line}"
            )
            category = source_categories[source_category]
            snapshot = row.get("category_path_snapshot")
            snapshot_name: object | None = None
            if isinstance(snapshot, Mapping):
                snapshot_name = (
                    snapshot.get("secondary")
                    or snapshot.get("primary")
                    or snapshot.get("name")
                )
            parent = category.get("parent_id")
            expected_versions[(str(book_id), str(category_id), str(version_id))] = {
                "book_id": book_id,
                "category_id": category_id,
                "category_version_id": version_id,
                "parent_category_id": (
                    None
                    if parent is None
                    else _deterministic_uuid("category", source_book, str(parent))
                ),
                "name": str(snapshot_name or category["name"]),
                "status": "archived",
                "change_reason_code": "backfill_line_snapshot",
            }

    _compare_expected_rows(
        expected=expected_categories,
        actual_rows=target.get("categories", []),
        key_fields=("book_id", "category_id"),
        compared_fields=(
            "parent_category_id",
            "current_name",
            "current_version_id",
            "status",
        ),
        scope_name="source-category",
        missing_code="source_catalog_missing",
        unexpected_code="source_catalog_unexpected",
        mismatch_code="source_catalog_mismatch",
        issues=issues,
    )
    _compare_expected_rows(
        expected=expected_versions,
        actual_rows=target.get("category_versions", []),
        key_fields=("book_id", "category_id", "category_version_id"),
        compared_fields=(
            "parent_category_id",
            "name",
            "status",
            "change_reason_code",
        ),
        scope_name="source-category-version",
        missing_code="source_catalog_missing",
        unexpected_code="source_catalog_unexpected",
        mismatch_code="source_catalog_mismatch",
        issues=issues,
    )

    if source.get("investment_valuations"):
        _add(
            issues,
            "source_investment_history_unmapped",
            "source-table:investment_valuations",
            f"{len(source['investment_valuations'])} V1 valuation rows have no supported typed mapping",
        )

    try:
        source_schedule = _source_canonical_schedule(source)
    except (KeyError, TypeError, ValueError) as error:
        _add(
            issues,
            "source_schedule_semantics_invalid",
            "source-schedule",
            str(error),
        )
        source_schedule = ()
    ordered_classification_events = tuple(
        action.row for action in source_schedule if action.kind == "classification"
    )
    if not source_schedule:
        ordered_classification_events = tuple(
            sorted(
                source.get("classification_events", []),
                key=lambda row: (
                    _source_time(row.get("created_at")),
                    str(row["classification_event_id"]).encode("utf-8"),
                ),
            )
        )
    source_lines_by_identity = {
        (
            str(row["book_id"]),
            str(row["transaction_id"]),
            str(row["line_id"]),
        ): row
        for row in source.get("transaction_lines", [])
    }
    classification_state_by_line: dict[tuple[str, str, str], Row] = {}
    previous_transition_by_line: dict[
        tuple[str, str, str],
        tuple[tuple[object, object, object], tuple[object, object, object]],
    ] = {}
    for row in ordered_classification_events:
        if str(row.get("event_type")) != "reclassify":
            continue
        source_event_id = str(row.get("classification_event_id"))
        before = row.get("before")
        after = row.get("after")
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            continue
        before_identity = (before.get("transaction_id"), before.get("line_id"))
        after_identity = (after.get("transaction_id"), after.get("line_id"))
        if (
            any(value is None for value in before_identity)
            or before_identity != after_identity
        ):
            _add(
                issues,
                "source_classification_semantics_invalid",
                f"source-classification:{source_event_id}",
                "reclassification before/after identities differ or are absent",
            )
            continue
        line_key = (
            str(row["book_id"]),
            str(before_identity[0]),
            str(before_identity[1]),
        )
        previous = classification_state_by_line.setdefault(line_key, before)
        transition = (_classification_state(before), _classification_state(after))
        if (
            _classification_state(previous) != transition[0]
            and previous_transition_by_line.get(line_key) != transition
        ):
            _add(
                issues,
                "source_classification_chain_mismatch",
                f"source-classification:{source_event_id}",
                "audit before-state does not equal the preceding after-state",
            )
        classification_state_by_line[line_key] = after
        previous_transition_by_line[line_key] = transition

    for line_key, final_audit_state in classification_state_by_line.items():
        source_line = source_lines_by_identity.get(line_key)
        if source_line is None or _classification_state(
            source_line
        ) != _classification_state(final_audit_state):
            _add(
                issues,
                "source_classification_final_state_mismatch",
                f"source-line:{':'.join(line_key)}",
                "final audit after-state differs from the frozen transaction line",
            )

    initial_classification_by_line: dict[tuple[str, str], Row] = {}
    for row in ordered_classification_events:
        if str(row.get("event_type")) != "reclassify":
            continue
        before = row.get("before")
        if isinstance(before, Mapping):
            transaction_id = before.get("transaction_id")
            line_id = before.get("line_id")
            if transaction_id is not None and line_id is not None:
                initial_classification_by_line.setdefault(
                    (str(transaction_id), str(line_id)), before
                )

    expected_transactions: dict[tuple[str, ...], Mapping[str, object]] = {}
    expected_postings: dict[tuple[str, ...], Mapping[str, object]] = {}
    expected_reversals: dict[tuple[str, ...], Mapping[str, object]] = {}
    expected_lines: dict[tuple[str, ...], Mapping[str, object]] = {}
    expected_events: dict[tuple[str, ...], Mapping[str, object]] = {}
    event_ids_by_action: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    expected_postings_by_source_transaction: dict[str, list[dict[str, object]]] = {}
    transaction_kind_by_source: dict[str, str] = {}
    primary_event_by_source: dict[str, dict[str, object]] = {}
    reporting_payload_by_source: dict[str, list[dict[str, object]]] = {}
    reporting_revision_by_source: dict[str, int] = {}
    book_positions: Counter[str] = Counter()
    book_hashes: dict[str, bytes] = defaultdict(lambda: ZERO_HASH)
    manifest_hash = snapshot_id.removeprefix("sha256:")
    actor_subject_id = f"backfill:{manifest_hash[:32]}"

    ordered_transactions = tuple(
        action.row for action in source_schedule if action.kind == "transaction"
    )
    if not source_schedule:
        ordered_transactions = tuple(
            sorted(
                source.get("transactions", []),
                key=lambda row: (
                    _source_time(row.get("occurred_at")),
                    str(row["transaction_id"]).encode("utf-8"),
                ),
            )
        )

    for transaction in ordered_transactions:
        action_identity = _source_action_identity("transaction", transaction)
        source_transaction_id = str(transaction["transaction_id"])
        source_book = str(transaction["book_id"])
        book_id = _deterministic_uuid("book", source_book)
        transaction_id = _deterministic_uuid(
            "transaction", snapshot_id, source_book, source_transaction_id
        )
        effective_at = _source_time(transaction.get("occurred_at"))
        source_postings = sorted(
            postings_by_transaction.get(source_transaction_id, []),
            key=lambda row: (int(row["position"]), str(row["id"])),
        )
        try:
            normalized_source_postings = [
                _expected_posting(
                    row,
                    transaction=transaction,
                    scales=scales,
                    position=position,
                )
                for position, row in enumerate(source_postings)
            ]
        except (KeyError, TypeError, ValueError) as error:
            _add(
                issues,
                "source_posting_semantics_invalid",
                f"source-transaction:{source_transaction_id}",
                str(error),
            )
            continue

        original_source_id = transaction.get("reverses_transaction_id")
        if original_source_id is None:
            projected_postings = normalized_source_postings
            if source_transaction_id in pure_fx_transactions:
                transaction_kind = "fx"
                operation = "journal.fx"
            else:
                transaction_kind = _source_kind(transaction.get("purpose"))
                operation = "journal.post"
            event_type = "JournalTransactionPosted"
        else:
            original_source_key = str(original_source_id)
            original_postings = expected_postings_by_source_transaction.get(
                original_source_key
            )
            original_event = primary_event_by_source.get(original_source_key)
            if original_postings is None or original_event is None:
                _add(
                    issues,
                    "source_reversal_semantics_invalid",
                    f"source-reversal:{source_transaction_id}",
                    "reversal source is missing",
                )
                continue
            expected_source_inverse = sorted(
                (
                    _identity(posting["account_id"]),
                    str(posting["asset_code"]),
                    "credit" if posting["side"] == "debit" else "debit",
                    int(posting["units"]),
                )
                for posting in original_postings
            )
            actual_source_inverse = sorted(
                (
                    _identity(posting["account_id"]),
                    str(posting["asset_code"]),
                    str(posting["side"]),
                    int(posting["units"]),
                )
                for posting in normalized_source_postings
            )
            if expected_source_inverse != actual_source_inverse:
                _add(
                    issues,
                    "source_reversal_semantics_invalid",
                    f"source-reversal:{source_transaction_id}",
                    "V1 reversal postings are not the exact inverse",
                )
            reverse_namespace = _EVENT_NAMESPACES["journal.reverse"]
            projected_postings = [
                {
                    **posting,
                    "posting_id": uuid5(
                        reverse_namespace,
                        f"{transaction_id}:posting:{posting['posting_id']}",
                    ),
                    "side": ("credit" if posting["side"] == "debit" else "debit"),
                }
                for posting in original_postings
            ]
            transaction_kind = transaction_kind_by_source[original_source_key]
            operation = "journal.reverse"
            event_type = "JournalTransactionReversed"

        command_id = _deterministic_uuid(
            "command",
            snapshot_id,
            source_book,
            source_transaction_id,
            operation,
        )
        event_id = (
            _deterministic_uuid(
                "event",
                snapshot_id,
                source_book,
                source_transaction_id,
                "journal.fx",
            )
            if operation == "journal.fx"
            else uuid5(_EVENT_NAMESPACES[operation], str(command_id))
        )
        book_key = str(book_id)
        book_positions[book_key] += 1
        if operation in {"journal.post", "journal.fx"}:
            payload: dict[str, object] = {
                "transaction_id": str(transaction_id),
                "kind": transaction_kind,
                "postings": [
                    {
                        "posting_id": str(posting["posting_id"]),
                        "position": int(posting["posting_position"]),
                        "account_id": str(posting["account_id"]),
                        "asset_code": str(posting["asset_code"]),
                        "side": str(posting["side"]),
                        "units": str(posting["units"]),
                    }
                    for posting in projected_postings
                ],
                "description_ref": None,
                "external_references": [],
            }
            causation_event_id = None
        else:
            original_source_key = str(original_source_id)
            original_event = primary_event_by_source[original_source_key]
            original_transaction_id = _deterministic_uuid(
                "transaction", snapshot_id, source_book, original_source_key
            )
            payload = {
                "reversal_transaction_id": str(transaction_id),
                "reverses_transaction_id": str(original_transaction_id),
                "original_event_id": str(original_event["event_id"]),
                "original_event_hash": bytes(original_event["event_hash"]).hex(),
                "reason_code": "import_correction",
                "inverse_postings": [
                    {
                        "posting_id": str(posting["posting_id"]),
                        "position": int(posting["posting_position"]),
                        "account_id": str(posting["account_id"]),
                        "asset_code": str(posting["asset_code"]),
                        "side": str(posting["side"]),
                        "units": str(posting["units"]),
                    }
                    for posting in projected_postings
                ],
                "description_ref": None,
            }
            causation_event_id = UUID(str(original_event["event_id"]))
        primary_event = _expected_event(
            event_id=event_id,
            book_id=book_id,
            book_position=book_positions[book_key],
            stream_type="journal_transaction",
            stream_id=transaction_id,
            event_type=event_type,
            command_id=command_id,
            actor_subject_id=actor_subject_id,
            causation_event_id=causation_event_id,
            effective_at=effective_at,
            payload=payload,
            previous_hash=book_hashes[book_key],
        )
        book_hashes[book_key] = bytes(primary_event["event_hash"])
        primary_event_by_source[source_transaction_id] = primary_event
        expected_events[(str(event_id),)] = primary_event
        event_ids_by_action[action_identity].append(str(event_id))
        expected_postings_by_source_transaction[source_transaction_id] = list(
            projected_postings
        )
        transaction_kind_by_source[source_transaction_id] = transaction_kind
        expected_transactions[(str(book_id), str(transaction_id))] = {
            "book_id": book_id,
            "transaction_id": transaction_id,
            "source_event_id": event_id,
            "source_position": book_positions[book_key],
            "effective_at": effective_at,
            "transaction_kind": transaction_kind,
            "description_ref": None,
        }
        for posting in projected_postings:
            posting_key = (
                str(book_id),
                str(transaction_id),
                str(posting["posting_id"]),
            )
            expected_postings[posting_key] = {
                "book_id": book_id,
                "transaction_id": transaction_id,
                **posting,
            }
        if original_source_id is not None:
            original_source_key = str(original_source_id)
            original_event = primary_event_by_source[original_source_key]
            original_transaction_id = _deterministic_uuid(
                "transaction", snapshot_id, source_book, original_source_key
            )
            expected_reversals[(str(book_id), str(transaction_id))] = {
                "book_id": book_id,
                "reversal_transaction_id": transaction_id,
                "original_transaction_id": original_transaction_id,
                "source_event_id": event_id,
                "original_event_id": original_event["event_id"],
                "original_event_hash": original_event["event_hash"],
                "reason_code": "import_correction",
            }

        source_lines = sorted(
            lines_by_transaction.get(source_transaction_id, []),
            key=lambda row: (int(row["position"]), str(row["line_id"])),
        )
        restored_source_lines: list[Row] = []
        for line in source_lines:
            initial = initial_classification_by_line.get(
                (source_transaction_id, str(line["line_id"]))
            )
            if initial is None:
                restored_source_lines.append(line)
                continue
            restored = dict(line)
            restored["category_id"] = initial.get("category_id")
            restored["category_version_id"] = initial.get("category_version_id")
            restored["category_path_snapshot"] = initial.get("category_path_snapshot")
            restored_source_lines.append(restored)
        source_lines = restored_source_lines
        category_lines = [
            line for line in source_lines if _historical_reporting_kind(line) is None
        ]
        historical_reporting_lines = [
            line
            for line in source_lines
            if _historical_reporting_kind(line) is not None
        ]
        reporting_payload_lines: list[dict[str, object]] = []
        for position, line in enumerate(category_lines):
            source_category = _category_source_for_line(line, source_category_versions)
            if source_category is None:
                continue
            source_line_id = str(line["line_id"])
            line_id = _deterministic_uuid(
                "line", source_book, source_transaction_id, source_line_id
            )
            line_version_id = _deterministic_uuid(
                "line_version",
                source_book,
                source_transaction_id,
                source_line_id,
                str(line.get("version", 1)),
            )
            source_catalog_id = line.get("category_version_id")
            if source_catalog_id is None:
                source_catalog_id = f"line-snapshot:{source_line_id}"
            catalog_id = _deterministic_uuid(
                "category_version", source_book, str(source_catalog_id)
            )
            dimension_id = _deterministic_uuid("category", source_book, source_category)
            line_value = {
                "line_id": str(line_id),
                "line_version_id": str(line_version_id),
                "catalog_id": str(catalog_id),
                "position": position,
                "asset_code": str(line["currency"]),
                "units": str(
                    _source_units(
                        line.get("amount"), scale=scales[str(line["currency"])]
                    )
                ),
                "line_kind": _source_line_kind(line.get("line_type")),
                "dimension": "category",
                "dimension_id": str(dimension_id),
                "description_ref": None,
            }
            reporting_payload_lines.append(line_value)

        if reporting_payload_lines:
            reporting_command_id = _deterministic_uuid(
                "command",
                snapshot_id,
                source_book,
                source_transaction_id,
                "reporting.assign",
            )
            reporting_event_id = uuid5(
                _EVENT_NAMESPACES["reporting.assign"], str(reporting_command_id)
            )
            book_positions[book_key] += 1
            reporting_event = _expected_event(
                event_id=reporting_event_id,
                book_id=book_id,
                book_position=book_positions[book_key],
                stream_type="reporting_lines",
                stream_id=transaction_id,
                event_type="ReportingLinesAssigned",
                command_id=reporting_command_id,
                actor_subject_id=actor_subject_id,
                causation_event_id=event_id,
                effective_at=effective_at,
                payload={
                    "transaction_id": str(transaction_id),
                    "classification_revision": 1,
                    "lines": reporting_payload_lines,
                },
                previous_hash=book_hashes[book_key],
            )
            book_hashes[book_key] = bytes(reporting_event["event_hash"])
            expected_events[(str(reporting_event_id),)] = reporting_event
            event_ids_by_action[action_identity].append(str(reporting_event_id))
            reporting_payload_by_source[source_transaction_id] = [
                dict(line) for line in reporting_payload_lines
            ]
            reporting_revision_by_source[source_transaction_id] = 1
            for line in reporting_payload_lines:
                line_id = str(line["line_id"])
                expected_lines[(str(book_id), str(transaction_id), line_id)] = {
                    "book_id": book_id,
                    "transaction_id": transaction_id,
                    "classification_revision": 1,
                    "line_id": line["line_id"],
                    "line_version_id": line["line_version_id"],
                    "catalog_id": line["catalog_id"],
                    "line_position": line["position"],
                    "asset_code": line["asset_code"],
                    "units": int(str(line["units"])),
                    "line_kind": line["line_kind"],
                    "dimension": line["dimension"],
                    "dimension_id": line["dimension_id"],
                    "description_ref": None,
                    "source_event_id": reporting_event_id,
                }

        for line in historical_reporting_lines:
            source_line_id = str(line["line_id"])
            historical_event_id = _deterministic_uuid(
                "event",
                snapshot_id,
                source_book,
                source_transaction_id,
                source_line_id,
                "historical-reporting-line",
            )
            historical_stream_id = _deterministic_uuid(
                "line", source_book, source_transaction_id, source_line_id
            )
            historical_command_id = _deterministic_uuid(
                "command",
                snapshot_id,
                source_book,
                source_transaction_id,
                source_line_id,
                "historical-reporting-import",
            )
            book_positions[book_key] += 1
            historical_event = _expected_event(
                event_id=historical_event_id,
                book_id=book_id,
                book_position=book_positions[book_key],
                stream_type="historical_reporting",
                stream_id=historical_stream_id,
                event_type="HistoricalReportingLineImported",
                command_id=historical_command_id,
                actor_subject_id=actor_subject_id,
                causation_event_id=event_id,
                effective_at=effective_at,
                payload={
                    "source_line_id": source_line_id,
                    "source_transaction_id": source_transaction_id,
                    "transaction_id": str(transaction_id),
                    "line_kind": _historical_reporting_kind(line),
                    "position": int(line["position"]),
                    "asset_code": str(line["currency"]),
                    "amount": _source_decimal_payload(line.get("amount")),
                    "source_version": int(line.get("version", 1)),
                    "source_row_hash": _source_object_hash(line),
                },
                previous_hash=book_hashes[book_key],
            )
            book_hashes[book_key] = bytes(historical_event["event_hash"])
            expected_events[(str(historical_event_id),)] = historical_event
            event_ids_by_action[action_identity].append(str(historical_event_id))

    for row in ordered_classification_events:
        action_identity = _source_action_identity("classification", row)
        source_event_id = str(row["classification_event_id"])
        source_book = str(row["book_id"])
        book_id = _deterministic_uuid("book", source_book)
        book_key = str(book_id)
        historical_event_id = _deterministic_uuid(
            "event",
            snapshot_id,
            source_book,
            source_event_id,
            "historical-category-activity",
        )
        historical_command_id = _deterministic_uuid(
            "command",
            snapshot_id,
            source_book,
            source_event_id,
            "historical-category-import",
        )
        activity_kind = str(row.get("event_type"))
        before = row.get("before")
        after = row.get("after")
        rollback = row.get("rollback")
        if not all(isinstance(value, Mapping) for value in (before, after, rollback)):
            _add(
                issues,
                "source_classification_semantics_invalid",
                f"source-classification:{source_event_id}",
                "classification snapshots must be objects",
            )
            continue
        historical_causation_event_id: UUID | None = None
        if activity_kind == "reclassify":
            source_transaction_id = str(after.get("transaction_id"))
            source_line_id = str(after.get("line_id"))
            current_lines = reporting_payload_by_source.get(source_transaction_id)
            current_revision = reporting_revision_by_source.get(source_transaction_id)
            primary_event = primary_event_by_source.get(source_transaction_id)
            if (
                current_lines is None
                or current_revision is None
                or primary_event is None
            ):
                _add(
                    issues,
                    "source_classification_semantics_invalid",
                    f"source-classification:{source_event_id}",
                    "reclassification target has no source-derived reporting state",
                )
                continue
            target_transaction_id = _deterministic_uuid(
                "transaction", snapshot_id, source_book, source_transaction_id
            )
            target_line_id = _deterministic_uuid(
                "line", source_book, source_transaction_id, source_line_id
            )
            next_lines: list[dict[str, object]] = []
            replaced = False
            for current in current_lines:
                updated = dict(current)
                if str(current["line_id"]) == str(target_line_id):
                    replaced = True
                    updated["line_version_id"] = str(
                        _deterministic_uuid(
                            "line_version",
                            source_book,
                            source_transaction_id,
                            source_line_id,
                            source_event_id,
                            str(row["version"]),
                        )
                    )
                    updated["catalog_id"] = str(
                        _deterministic_uuid(
                            "category_version",
                            source_book,
                            str(after["category_version_id"]),
                        )
                    )
                    updated["dimension_id"] = str(
                        _deterministic_uuid(
                            "category", source_book, str(after["category_id"])
                        )
                    )
                next_lines.append(updated)
            if not replaced:
                _add(
                    issues,
                    "source_classification_semantics_invalid",
                    f"source-classification:{source_event_id}",
                    "reclassification target line is absent",
                )
                continue
            revision = current_revision + 1
            reporting_command_id = _deterministic_uuid(
                "command",
                snapshot_id,
                source_book,
                source_event_id,
                "reporting.reclassify",
            )
            reporting_event_id = uuid5(
                _EVENT_NAMESPACES["reporting.assign"], str(reporting_command_id)
            )
            effective_at = _source_time(row.get("created_at"))
            book_positions[book_key] += 1
            reporting_event = _expected_event(
                event_id=reporting_event_id,
                book_id=book_id,
                book_position=book_positions[book_key],
                stream_type="reporting_lines",
                stream_id=target_transaction_id,
                stream_version=revision,
                event_type="ReportingLinesAssigned",
                command_id=reporting_command_id,
                actor_subject_id=actor_subject_id,
                causation_event_id=UUID(str(primary_event["event_id"])),
                effective_at=effective_at,
                payload={
                    "transaction_id": str(target_transaction_id),
                    "classification_revision": revision,
                    "lines": next_lines,
                },
                previous_hash=book_hashes[book_key],
            )
            book_hashes[book_key] = bytes(reporting_event["event_hash"])
            expected_events[(str(reporting_event_id),)] = reporting_event
            event_ids_by_action[action_identity].append(str(reporting_event_id))
            reporting_payload_by_source[source_transaction_id] = next_lines
            reporting_revision_by_source[source_transaction_id] = revision
            for line in next_lines:
                line_id = str(line["line_id"])
                expected_lines[(str(book_id), str(target_transaction_id), line_id)] = {
                    "book_id": book_id,
                    "transaction_id": target_transaction_id,
                    "classification_revision": revision,
                    "line_id": line["line_id"],
                    "line_version_id": line["line_version_id"],
                    "catalog_id": line["catalog_id"],
                    "line_position": line["position"],
                    "asset_code": line["asset_code"],
                    "units": int(str(line["units"])),
                    "line_kind": line["line_kind"],
                    "dimension": line["dimension"],
                    "dimension_id": line["dimension_id"],
                    "description_ref": line["description_ref"],
                    "source_event_id": reporting_event_id,
                }
            historical_causation_event_id = reporting_event_id
        elif activity_kind != "create":
            _add(
                issues,
                "source_classification_semantics_invalid",
                f"source-classification:{source_event_id}",
                f"unsupported classification activity {activity_kind}",
            )
            continue
        book_positions[book_key] += 1
        historical_event = _expected_event(
            event_id=historical_event_id,
            book_id=book_id,
            book_position=book_positions[book_key],
            stream_type="historical_category",
            stream_id=historical_event_id,
            event_type="HistoricalCategoryActivityImported",
            command_id=historical_command_id,
            actor_subject_id=actor_subject_id,
            causation_event_id=historical_causation_event_id,
            effective_at=_source_time(row.get("created_at")),
            payload={
                "source_event_id": source_event_id,
                "activity_kind": activity_kind,
                "source_category_id": str(row["source_category_id"]),
                "target_category_id": (
                    None
                    if row.get("target_category_id") is None
                    else str(row["target_category_id"])
                ),
                "affected_line_count": int(row["affected_line_count"]),
                "source_actor_hash": _source_actor_hash(row.get("created_by")),
                "source_version": int(row["version"]),
                "before_hash": _source_object_hash(before),
                "after_hash": _source_object_hash(after),
                "rollback_hash": _source_object_hash(rollback),
                "source_row_hash": _source_object_hash(row),
            },
            previous_hash=book_hashes[book_key],
        )
        book_hashes[book_key] = bytes(historical_event["event_hash"])
        expected_events[(str(historical_event_id),)] = historical_event
        event_ids_by_action[action_identity].append(str(historical_event_id))

    ordered_investment_events = tuple(
        action.row for action in source_schedule if action.kind == "investment"
    )
    if not source_schedule:
        ordered_investment_events = tuple(
            sorted(
                source.get("investment_events", []),
                key=lambda row: (
                    _source_time(row.get("occurred_at")),
                    str(row["event_id"]).encode("utf-8"),
                ),
            )
        )
    for row in ordered_investment_events:
        action_identity = _source_action_identity("investment", row)
        source_event_id = str(row["event_id"])
        source_book = str(row["book_id"])
        book_id = _deterministic_uuid("book", source_book)
        book_key = str(book_id)
        event_id = _deterministic_uuid(
            "event",
            snapshot_id,
            source_book,
            source_event_id,
            "historical-investment-activity",
        )
        stream_id = _deterministic_uuid(
            "event",
            snapshot_id,
            source_book,
            source_event_id,
            "historical-investment-stream",
        )
        command_id = _deterministic_uuid(
            "command",
            snapshot_id,
            source_book,
            source_event_id,
            "historical-investment-import",
        )
        book_positions[book_key] += 1
        investment_event = _expected_event(
            event_id=event_id,
            book_id=book_id,
            book_position=book_positions[book_key],
            stream_type="historical_investment",
            stream_id=stream_id,
            event_type="HistoricalInvestmentActivityImported",
            command_id=command_id,
            actor_subject_id=actor_subject_id,
            causation_event_id=None,
            effective_at=_source_time(row.get("occurred_at")),
            payload={
                "source_event_id": source_event_id,
                "source_account_id": str(row["account_id"]),
                "activity_kind": str(row["event_type"]),
                "settlement_asset_code": str(row["currency"]),
                "cash_amount": _source_decimal_payload(row.get("amount")),
                "quantity": (
                    None
                    if row.get("units") is None
                    else _source_decimal_payload(row.get("units"))
                ),
                "nav": (
                    None
                    if row.get("nav") is None
                    else _source_decimal_payload(row.get("nav"))
                ),
                "source_version": int(row["version"]),
                "source_row_hash": _source_object_hash(row),
            },
            previous_hash=book_hashes[book_key],
        )
        book_hashes[book_key] = bytes(investment_event["event_hash"])
        expected_events[(str(event_id),)] = investment_event
        event_ids_by_action[action_identity].append(str(event_id))

    if source_schedule:
        canonical_events: dict[tuple[str, ...], Mapping[str, object]] = {}
        canonical_positions: Counter[str] = Counter()
        canonical_hashes: dict[str, bytes] = defaultdict(lambda: ZERO_HASH)
        for action in source_schedule:
            for source_event_id in event_ids_by_action.get(action.identity, []):
                event_key = (source_event_id,)
                event = dict(expected_events[event_key])
                book_key = str(event["book_id"])
                original_event: Mapping[str, object] | None = None
                if str(event["event_type"]) == "JournalTransactionReversed":
                    payload = event.get("payload")
                    if not isinstance(payload, Mapping):
                        raise ValueError(
                            "source-derived reversal payload is not an object"
                        )
                    patched_payload = dict(payload)
                    original_event = canonical_events.get(
                        (str(patched_payload["original_event_id"]),)
                    )
                    if original_event is None:
                        _add(
                            issues,
                            "source_schedule_semantics_invalid",
                            f"source-event:{source_event_id}",
                            "reversal was scheduled before its original event",
                        )
                    else:
                        patched_payload["original_event_hash"] = bytes(
                            original_event["event_hash"]
                        ).hex()
                        event["payload"] = patched_payload

                canonical_positions[book_key] += 1
                event["book_position"] = canonical_positions[book_key]
                event["previous_hash"] = canonical_hashes[book_key]
                event["event_hash"] = reference_event_hash(event)
                canonical_hashes[book_key] = bytes(event["event_hash"])
                canonical_events[event_key] = event

                if str(event["stream_type"]) != "journal_transaction":
                    continue
                transaction_key = (book_key, str(event["stream_id"]))
                transaction = expected_transactions.get(transaction_key)
                if transaction is not None:
                    patched_transaction = dict(transaction)
                    patched_transaction["source_position"] = event["book_position"]
                    expected_transactions[transaction_key] = patched_transaction
                if original_event is not None:
                    reversal = expected_reversals.get(transaction_key)
                    if reversal is not None:
                        patched_reversal = dict(reversal)
                        patched_reversal["original_event_hash"] = original_event[
                            "event_hash"
                        ]
                        expected_reversals[transaction_key] = patched_reversal

        if set(canonical_events) != set(expected_events):
            _add(
                issues,
                "source_schedule_semantics_invalid",
                "source-schedule",
                "source-derived events are not covered by canonical actions",
            )
        else:
            expected_events = canonical_events

    _compare_expected_rows(
        expected=expected_transactions,
        actual_rows=target.get("journal_transactions", []),
        key_fields=("book_id", "transaction_id"),
        compared_fields=(
            "source_event_id",
            "source_position",
            "effective_at",
            "transaction_kind",
            "description_ref",
        ),
        scope_name="source-transaction",
        missing_code="source_transaction_missing",
        unexpected_code="source_transaction_unexpected",
        mismatch_code="source_transaction_mismatch",
        issues=issues,
    )
    actual_transactions = {
        (_identity(row["book_id"]), _identity(row["transaction_id"])): row
        for row in target.get("journal_transactions", [])
    }
    for key, expected in expected_transactions.items():
        actual = actual_transactions.get(key)
        if actual is not None and _exact_value(
            actual.get("effective_at")
        ) != _exact_value(expected.get("effective_at")):
            _add(
                issues,
                "source_effective_time_mismatch",
                f"source-transaction:{':'.join(key)}",
                "target effective time differs from the frozen V1 transaction",
            )
    _compare_expected_rows(
        expected=expected_postings,
        actual_rows=target.get("journal_postings", []),
        key_fields=("book_id", "transaction_id", "posting_id"),
        compared_fields=(
            "posting_position",
            "account_id",
            "asset_code",
            "side",
            "units",
        ),
        scope_name="source-posting",
        missing_code="source_posting_missing",
        unexpected_code="source_posting_unexpected",
        mismatch_code="source_posting_mismatch",
        issues=issues,
    )
    _compare_expected_rows(
        expected=expected_reversals,
        actual_rows=target.get("transaction_reversals", []),
        key_fields=("book_id", "reversal_transaction_id"),
        compared_fields=(
            "original_transaction_id",
            "source_event_id",
            "original_event_id",
            "original_event_hash",
            "reason_code",
        ),
        scope_name="source-reversal",
        missing_code="source_reversal_missing",
        unexpected_code="source_reversal_unexpected",
        mismatch_code="source_reversal_mismatch",
        issues=issues,
    )
    _compare_expected_rows(
        expected=expected_lines,
        actual_rows=target.get("reporting_lines", []),
        key_fields=("book_id", "transaction_id", "line_id"),
        compared_fields=(
            "classification_revision",
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
        scope_name="source-reporting-line",
        missing_code="source_reporting_line_missing",
        unexpected_code="source_reporting_line_unexpected",
        mismatch_code="source_reporting_line_mismatch",
        issues=issues,
    )
    _compare_expected_rows(
        expected=expected_events,
        actual_rows=target.get("ledger_events", []),
        key_fields=("event_id",),
        compared_fields=(
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
            "payload",
            "previous_hash",
            "event_hash",
        ),
        scope_name="source-event",
        missing_code="source_event_missing",
        unexpected_code="source_event_unexpected",
        mismatch_code="source_event_mismatch",
        issues=issues,
    )
    return tuple(sorted(set(issues)))


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
    "verify_source_target_semantics",
]
