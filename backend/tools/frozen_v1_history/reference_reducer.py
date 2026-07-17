from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
from types import MappingProxyType
from typing import TypeAlias
from uuid import UUID, uuid5


JSONScalar: TypeAlias = str | int | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
RawObject: TypeAlias = Mapping[str, object]

_EVENT_HASH_DOMAIN = b"track-anywhere:v2:ledger-event-hash:sha256:v1"
_DESCRIPTION_AGGREGATE_DOMAIN = (
    b"track-anywhere:frozen-v1:description-aggregate:sha256:v1\0"
)
_OPAQUE_REFERENCE_DOMAIN = b"track-anywhere:frozen-v1:opaque-reference:v1\0"
_ZERO_HASH = "0" * 64
_FROZEN_UUID_NAMESPACE = UUID("3f021172-6aa9-5b36-9208-f238bc35c596")
_FROZEN_ACTOR_SUBJECT_ID = "offline:frozen-v1-history"
_SOURCE_DECIMAL = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$",
    flags=re.ASCII,
)
_EXPECTED_ROOT_KEYS = frozenset(
    {
        "accounts",
        "archive",
        "assets",
        "card_review_hash",
        "categories",
        "contract_version",
        "descriptions",
        "events",
        "expected_terminal_hash",
        "manifest_hash",
        "quarantine_count",
        "source_dump_hash",
        "target_book_id",
    }
)
_PINNED_COUNTS = {
    "accounts": 121,
    "archives": 1,
    "assets": 20,
    "categories": 37,
    "category_versions": 37,
    "credit_card_transactions": 0,
    "descriptions": 138,
    "journal_postings": 290,
    "journal_transactions": 138,
    "ledger_events": 176,
    "quarantine": 0,
    "reporting_lines": 38,
    "reversals": 8,
    "synchronous_projection_applied_events": 176,
}


class ReferenceReductionError(ValueError):
    """A secret-free failure from the independent frozen-history reducer."""


@dataclass(frozen=True, slots=True)
class ReferenceLedgerFacts:
    book_id: str
    plan_hash: str
    terminal_position: int
    terminal_hash: str
    counts: Mapping[str, int]
    hashes: Mapping[str, str]
    description_ids: tuple[str, ...] = field(repr=False)
    description_aggregate_sha256: str
    archive_id: str = field(repr=False)
    archive_plaintext_sha256: str
    archive_metadata_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        object.__setattr__(self, "hashes", MappingProxyType(dict(self.hashes)))


@dataclass(frozen=True, slots=True)
class SourceLedgerFacts:
    """Secret-free expected target semantics reduced directly from raw V1 rows."""

    book_id: str
    terminal_position: int
    terminal_hash: str
    counts: Mapping[str, int]
    hashes: Mapping[str, str]
    description_ids: tuple[str, ...] = field(repr=False)
    description_aggregate_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        object.__setattr__(self, "hashes", MappingProxyType(dict(self.hashes)))


@dataclass(frozen=True, slots=True)
class _JournalSource:
    event_id: str
    event_hash: str
    transaction_id: str
    transaction_kind: str
    effective_date: date
    postings: tuple[dict[str, JSONValue], ...] = field(repr=False)
    is_reversal: bool


@dataclass(frozen=True, slots=True)
class _RawSourceEvent:
    event_id: str
    event_hash: str
    transaction_id: str
    transaction_kind: str
    postings: tuple[dict[str, JSONValue], ...] = field(repr=False)
    event_type: str


def _fail(code: str) -> None:
    raise ReferenceReductionError(code)


def _object(value: object, code: str) -> RawObject:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(code)
    return value


def _text(value: object, code: str) -> str:
    if type(value) is not str or not value:
        _fail(code)
    return value


def _integer(value: object, code: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        _fail(code)
    return value


def _boolean(value: object, code: str) -> bool:
    if type(value) is not bool:
        _fail(code)
    return value


def _uuid(value: object, code: str) -> str:
    raw = _text(value, code)
    try:
        parsed = UUID(raw)
    except (TypeError, ValueError, AttributeError):
        _fail(code)
    if str(parsed) != raw:
        _fail(code)
    return raw


def _optional_uuid(value: object, code: str) -> str | None:
    return None if value is None else _uuid(value, code)


def _sha256(value: object, code: str) -> str:
    raw = _text(value, code)
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        _fail(code)
    return raw


def _canonical_timestamp(value: object, code: str) -> tuple[str, date]:
    raw = _text(value, code)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        normalized = parsed.astimezone(UTC)
        canonical = normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        serialized = (
            normalized.strftime("%Y-%m-%dT%H:%M:%SZ")
            if normalized.microsecond == 0
            else canonical
        )
    except (TypeError, ValueError, OverflowError):
        _fail(code)
    if raw != serialized:
        _fail(code)
    return canonical, normalized.date()


def _positive_units(value: object, code: str) -> int:
    raw = _text(value, code)
    if not raw.isascii() or not raw.isdecimal() or raw.startswith("0"):
        _fail(code)
    units = int(raw)
    if units <= 0 or str(units) != raw:
        _fail(code)
    return units


def _canonical_json_bytes(value: JSONValue) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail("canonical_json_invalid")


def _json_value(value: object, code: str) -> JSONValue:
    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return value  # type: ignore[return-value]
    if value_type is list:
        return [_json_value(item, code) for item in value]  # type: ignore[arg-type]
    if value_type is dict and all(type(key) is str for key in value):
        return {key: _json_value(item, code) for key, item in value.items()}
    _fail(code)


def _hash_rows(rows: Sequence[dict[str, JSONValue]]) -> str:
    return hashlib.sha256(_canonical_json_bytes(list(rows))).hexdigest()


def _event_order_hash(rows: Sequence[dict[str, JSONValue]]) -> str:
    return _hash_rows(
        [
            {
                "book_position": row["book_position"],
                "event_id": row["event_id"],
            }
            for row in rows
        ]
    )


def _event_payloads_hash(rows: Sequence[dict[str, JSONValue]]) -> str:
    return _hash_rows(
        [
            {
                "event_id": row["event_id"],
                "event_schema_version": row["event_schema_version"],
                "event_type": row["event_type"],
                "payload": row["payload"],
            }
            for row in rows
        ]
    )


def _combined_hash(**hashes: str) -> str:
    return hashlib.sha256(
        _canonical_json_bytes({key: hashes[key] for key in sorted(hashes)})
    ).hexdigest()


def _decode_canonical_bytes(value: object, code: str) -> bytes:
    encoded = _text(value, code)
    try:
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (TypeError, ValueError):
        _fail(code)
    if not decoded:
        _fail(code)
    return decoded


def _description_aggregate(
    descriptions: Sequence[tuple[str, bytes]],
) -> str:
    digest = hashlib.sha256()
    digest.update(_DESCRIPTION_AGGREGATE_DOMAIN)
    for sidecar_id, plaintext in sorted(descriptions):
        identity = sidecar_id.encode("ascii")
        digest.update(len(identity).to_bytes(2, "big"))
        digest.update(identity)
        digest.update(len(plaintext).to_bytes(8, "big"))
        digest.update(plaintext)
    return digest.hexdigest()


def _event_hash(
    *,
    event: RawObject,
    book_id: str,
    effective_at: str,
    previous_hash: str,
    payload: dict[str, JSONValue],
) -> str:
    envelope: dict[str, JSONValue] = {
        "actor_subject_id": _text(
            event.get("actor_subject_id"), "event_envelope_invalid"
        ),
        "book_id": book_id,
        "book_position": _integer(
            event.get("book_position"), "event_envelope_invalid", minimum=1
        ),
        "causation_event_id": _optional_uuid(
            event.get("causation_event_id"), "event_envelope_invalid"
        ),
        "command_id": _uuid(event.get("command_id"), "event_envelope_invalid"),
        "correlation_id": _uuid(event.get("correlation_id"), "event_envelope_invalid"),
        "effective_at": effective_at,
        "event_id": _uuid(event.get("event_id"), "event_envelope_invalid"),
        "event_schema_version": _integer(
            event.get("event_schema_version"), "event_envelope_invalid", minimum=1
        ),
        "event_type": _text(event.get("event_type"), "event_envelope_invalid"),
        "previous_hash": previous_hash,
        "stream_id": _uuid(event.get("stream_id"), "event_envelope_invalid"),
        "stream_type": _text(event.get("stream_type"), "event_envelope_invalid"),
        "stream_version": _integer(
            event.get("stream_version"), "event_envelope_invalid", minimum=1
        ),
    }
    return hashlib.sha256(
        _EVENT_HASH_DOMAIN
        + b"\0"
        + _canonical_json_bytes(envelope)
        + b"\0"
        + _canonical_json_bytes(payload)
    ).hexdigest()


def _posting_rows(
    value: object,
    *,
    book_id: str,
    transaction_id: str,
    account_assets: Mapping[str, str],
    event_position: int,
    posting_ids: set[str],
    balance_units: dict[tuple[str, str], int],
    balance_positions: dict[tuple[str, str], int],
) -> tuple[dict[str, JSONValue], ...]:
    raw_postings = _sequence(value, "posting_shape_invalid")
    if len(raw_postings) < 2:
        _fail("posting_shape_invalid")
    rows: list[dict[str, JSONValue]] = []
    net_by_asset: dict[str, int] = {}
    for position, raw_posting in enumerate(raw_postings):
        posting = _object(raw_posting, "posting_shape_invalid")
        posting_id = _uuid(posting.get("posting_id"), "posting_shape_invalid")
        if posting_id in posting_ids:
            _fail("posting_identity_duplicate")
        posting_ids.add(posting_id)
        if (
            _integer(posting.get("position"), "posting_shape_invalid", minimum=0)
            != position
        ):
            _fail("posting_order_invalid")
        account_id = _uuid(posting.get("account_id"), "posting_shape_invalid")
        asset_code = _text(posting.get("asset_code"), "posting_shape_invalid")
        if account_assets.get(account_id) != asset_code:
            _fail("posting_account_asset_mismatch")
        side = _text(posting.get("side"), "posting_shape_invalid")
        if side not in {"debit", "credit"}:
            _fail("posting_side_invalid")
        units = _positive_units(posting.get("units"), "posting_units_invalid")
        signed = units if side == "debit" else -units
        net_by_asset[asset_code] = net_by_asset.get(asset_code, 0) + signed
        key = (account_id, asset_code)
        balance_units[key] = balance_units.get(key, 0) + signed
        balance_positions[key] = event_position
        rows.append(
            {
                "account_id": account_id,
                "asset_code": asset_code,
                "book_id": book_id,
                "position": position,
                "posting_id": posting_id,
                "side": side,
                "transaction_id": transaction_id,
                "units": str(units),
            }
        )
    if any(value != 0 for value in net_by_asset.values()):
        _fail("journal_not_balanced")
    return tuple(rows)


def _validate_inverse(
    source: _JournalSource,
    inverse: tuple[dict[str, JSONValue], ...],
) -> None:
    if len(source.postings) != len(inverse):
        _fail("reversal_posting_mismatch")
    for original, candidate in zip(source.postings, inverse, strict=True):
        expected_side = "credit" if original["side"] == "debit" else "debit"
        if (
            candidate["position"] != original["position"]
            or candidate["account_id"] != original["account_id"]
            or candidate["asset_code"] != original["asset_code"]
            or candidate["units"] != original["units"]
            or candidate["side"] != expected_side
            or candidate["posting_id"] == original["posting_id"]
        ):
            _fail("reversal_posting_mismatch")


def _month_start(value: date) -> str:
    return date(value.year, value.month, 1).isoformat()


def _source_rows(value: object, code: str) -> tuple[RawObject, ...]:
    return tuple(_object(item, code) for item in _sequence(value, code))


def _source_identity(value: object, code: str) -> str:
    if type(value) is str and value:
        return value
    if type(value) is int and value >= 0:
        return str(value)
    _fail(code)


def _source_optional_text(value: object, code: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail(code)
    return value


def _source_uuid(kind: str, *parts: str) -> str:
    if (
        not kind
        or not parts
        or any(type(part) is not str or not part for part in parts)
    ):
        _fail("source_identity_invalid")
    kind_namespace = uuid5(_FROZEN_UUID_NAMESPACE, kind)
    encoded = json.dumps(
        list(parts),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(kind_namespace, encoded))


def _source_timestamp(value: object, code: str) -> tuple[str, datetime, date]:
    raw = _text(value, code)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        normalized = parsed.astimezone(UTC)
        canonical = normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError, OverflowError):
        _fail(code)
    return canonical, normalized, normalized.date()


def _source_transaction_sort_key(row: RawObject) -> tuple[datetime, bytes]:
    _canonical, timestamp, _effective_date = _source_timestamp(
        row.get("occurred_at"), "source_transaction_invalid"
    )
    identity = _source_identity(row.get("transaction_id"), "source_transaction_invalid")
    return timestamp, identity.encode("utf-8")


def _source_transaction_kind(purpose: object, *, pure_fx: bool) -> str:
    if pure_fx:
        return "fx"
    normalized = str(purpose or "").casefold()
    if "opening" in normalized:
        return "opening"
    if "transfer" in normalized:
        return "transfer"
    if "adjust" in normalized:
        return "adjustment"
    return "standard"


def _source_pure_fx_transactions(
    *,
    transactions: Mapping[str, RawObject],
    postings_by_transaction: Mapping[str, list[RawObject]],
    accounts: Mapping[str, RawObject],
    asset_scales: Mapping[str, int],
) -> set[str]:
    pure_fx: set[str] = set()
    for transaction_id in transactions:
        candidates = postings_by_transaction.get(transaction_id, [])
        by_asset: dict[str, list[RawObject]] = {}
        for posting in candidates:
            asset_code = _text(posting.get("currency"), "source_posting_invalid")
            by_asset.setdefault(asset_code, []).append(posting)
        if (
            len(candidates) != 4
            or len(by_asset) != 2
            or any(len(group) != 2 for group in by_asset.values())
        ):
            continue
        system_sides: set[str] = set()
        valid = True
        for asset_code, group in by_asset.items():
            system = [
                posting
                for posting in group
                if accounts[
                    _source_identity(
                        posting.get("account_id"), "source_posting_invalid"
                    )
                ].get("type")
                == "system"
            ]
            if len(system) != 1:
                valid = False
                break
            other = group[0] if group[1] is system[0] else group[1]
            system_side, _ = _source_exact_amount(
                system[0].get("amount"),
                semantics=system[0].get("amount_semantics"),
                explicit_side=system[0].get("side"),
                ledger_scale=asset_scales[asset_code],
            )
            other_side, _ = _source_exact_amount(
                other.get("amount"),
                semantics=other.get("amount_semantics"),
                explicit_side=other.get("side"),
                ledger_scale=asset_scales[asset_code],
            )
            if system_side == other_side:
                valid = False
                break
            system_sides.add(system_side)
        if valid and system_sides == {"debit", "credit"}:
            pure_fx.add(transaction_id)
    return pure_fx


def _opaque_source_reference(kind: str, *parts: str) -> str:
    if (
        not kind
        or not parts
        or any(type(part) is not str or not part for part in parts)
    ):
        _fail("source_identity_invalid")
    digest = hashlib.sha256(
        _OPAQUE_REFERENCE_DOMAIN
        + kind.encode("ascii")
        + b"\0"
        + b"\0".join(part.encode("utf-8") for part in parts)
    ).hexdigest()
    return f"sha256:{digest}"


def _event_postings(
    rows: Sequence[dict[str, JSONValue]],
) -> list[JSONValue]:
    return [
        {
            "account_id": row["account_id"],
            "asset_code": row["asset_code"],
            "position": row["position"],
            "posting_id": row["posting_id"],
            "side": row["side"],
            "units": row["units"],
        }
        for row in rows
    ]


def _append_source_event(
    event_rows: list[dict[str, JSONValue]],
    *,
    book_id: str,
    event_id: str,
    stream_type: str,
    stream_id: str,
    event_type: str,
    payload: dict[str, JSONValue],
    command_id: str,
    causation_event_id: str | None,
    effective_at: str,
) -> dict[str, JSONValue]:
    position = len(event_rows) + 1
    previous_hash = _ZERO_HASH if not event_rows else str(event_rows[-1]["event_hash"])
    envelope: dict[str, object] = {
        "actor_subject_id": _FROZEN_ACTOR_SUBJECT_ID,
        "book_position": position,
        "causation_event_id": causation_event_id,
        "command_id": command_id,
        "correlation_id": command_id,
        "event_id": event_id,
        "event_schema_version": 1,
        "event_type": event_type,
        "stream_id": stream_id,
        "stream_type": stream_type,
        "stream_version": 1,
    }
    stored_hash = _event_hash(
        event=envelope,
        book_id=book_id,
        effective_at=effective_at,
        previous_hash=previous_hash,
        payload=payload,
    )
    row: dict[str, JSONValue] = {
        "actor_subject_id": _FROZEN_ACTOR_SUBJECT_ID,
        "book_id": book_id,
        "book_position": position,
        "causation_event_id": causation_event_id,
        "command_id": command_id,
        "correlation_id": command_id,
        "effective_at": effective_at,
        "event_hash": stored_hash,
        "event_id": event_id,
        "event_schema_version": 1,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
        "stream_id": stream_id,
        "stream_type": stream_type,
        "stream_version": 1,
    }
    event_rows.append(row)
    return row


def _source_exact_amount(
    amount: object,
    *,
    semantics: object,
    explicit_side: object,
    ledger_scale: int,
) -> tuple[str, int]:
    if (
        type(amount) is not str
        or len(amount) > 128
        or _SOURCE_DECIMAL.fullmatch(amount) is None
        or type(ledger_scale) is not int
        or not 0 <= ledger_scale <= 30
    ):
        _fail("source_amount_invalid")
    exponent_text = amount.lower().partition("e")[2]
    if exponent_text and abs(int(exponent_text)) > 100:
        _fail("source_amount_invalid")
    try:
        with localcontext() as context:
            context.prec = 100
            value = Decimal(amount)
            scaled = value * (Decimal(10) ** ledger_scale)
            integral = scaled.to_integral_value()
    except (InvalidOperation, OverflowError, ValueError):
        _fail("source_amount_invalid")
    if not value.is_finite() or scaled != integral or integral == 0:
        _fail("source_amount_invalid")
    units = abs(int(integral))
    if len(str(units)) > 38:
        _fail("source_amount_invalid")
    if semantics in {None, "legacy_signed"}:
        return ("debit" if integral > 0 else "credit"), units
    if semantics == "debit_credit":
        if amount.startswith("-") or explicit_side not in {"debit", "credit"}:
            _fail("source_amount_invalid")
        return str(explicit_side), units
    _fail("source_amount_invalid")


def _source_reporting_kind(value: object) -> str:
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


def _source_reversal_links(
    transactions: Mapping[str, RawObject],
) -> dict[str, str]:
    links: dict[str, str] = {}
    for transaction_id, row in transactions.items():
        direct = row.get("reverses_transaction_id")
        if direct is not None:
            links[transaction_id] = _source_identity(direct, "source_reversal_invalid")
    for original_id, row in transactions.items():
        reverse = row.get("reversed_by")
        if reverse is None:
            continue
        reverse_id = _source_identity(reverse, "source_reversal_invalid")
        if links.get(reverse_id, original_id) != original_id:
            _fail("source_reversal_invalid")
        links[reverse_id] = original_id
    if any(
        reverse_id not in transactions
        or original_id not in transactions
        or reverse_id == original_id
        for reverse_id, original_id in links.items()
    ):
        _fail("source_reversal_invalid")
    if len(set(links.values())) != len(links):
        _fail("source_reversal_invalid")
    return links


def reduce_frozen_source_rows(source: RawObject) -> SourceLedgerFacts:
    """Derive expected V2 financial facts from primitive canonical V1 rows."""

    root = _object(source, "source_shape_invalid")
    if set(root) != {"review", "snapshot_id", "tables", "target_book_id"}:
        _fail("source_shape_invalid")
    snapshot_id = _text(root.get("snapshot_id"), "source_shape_invalid")
    target_book_id = _uuid(root.get("target_book_id"), "source_shape_invalid")
    tables = _object(root.get("tables"), "source_shape_invalid")
    required_tables = {
        "accounts",
        "assets",
        "categories",
        "category_versions",
        "ledger_books",
        "postings",
        "transaction_lines",
        "transactions",
    }
    if set(tables) != required_tables:
        _fail("source_shape_invalid")
    rows = {
        name: _source_rows(tables[name], "source_shape_invalid")
        for name in required_tables
    }
    if len(rows["ledger_books"]) != 1:
        _fail("source_shape_invalid")
    source_book_id = _source_identity(
        rows["ledger_books"][0].get("book_id"), "source_shape_invalid"
    )

    asset_scales: dict[str, int] = {}
    for asset in rows["assets"]:
        asset_code = _text(asset.get("asset_code"), "source_asset_invalid")
        source_scale = _integer(asset.get("scale"), "source_asset_invalid", minimum=0)
        display_scale = _integer(
            asset.get("display_scale"), "source_asset_invalid", minimum=0
        )
        ledger_scale = max(source_scale, 8) if asset_code == "USDT" else source_scale
        if (
            asset_code in asset_scales
            or ledger_scale > 30
            or display_scale > ledger_scale
        ):
            _fail("source_asset_invalid")
        asset_scales[asset_code] = ledger_scale

    accounts: dict[str, RawObject] = {}
    for account in rows["accounts"]:
        source_id = _source_identity(
            account.get("account_id"), "source_account_invalid"
        )
        asset_code = _text(account.get("currency"), "source_account_invalid")
        if (
            source_id in accounts
            or asset_code not in asset_scales
            or _source_identity(account.get("book_id"), "source_account_invalid")
            != source_book_id
        ):
            _fail("source_account_invalid")
        accounts[source_id] = account

    transactions: dict[str, RawObject] = {}
    for transaction in rows["transactions"]:
        source_id = _source_identity(
            transaction.get("transaction_id"), "source_transaction_invalid"
        )
        if source_id in transactions:
            _fail("source_transaction_invalid")
        transactions[source_id] = transaction

    review = _object(root.get("review"), "source_review_invalid")
    if set(review) != {
        "exact_reversal_transaction_ids",
        "expected_card_balances",
        "posting_decisions",
        "retired_alias_account_ids",
    }:
        _fail("source_review_invalid")
    decisions: dict[str, tuple[str, str]] = {}
    for raw_decision in _source_rows(
        review.get("posting_decisions"), "source_review_invalid"
    ):
        posting_id = _source_identity(
            raw_decision.get("source_posting_id"), "source_review_invalid"
        )
        target_account = _source_identity(
            raw_decision.get("target_account_id"), "source_review_invalid"
        )
        target_side = _text(raw_decision.get("target_side"), "source_review_invalid")
        if (
            posting_id in decisions
            or target_account not in accounts
            or target_side not in {"debit", "credit"}
        ):
            _fail("source_review_invalid")
        decisions[posting_id] = (target_account, target_side)
    correction_sources = tuple(
        _source_identity(value, "source_review_invalid")
        for value in _sequence(
            review.get("exact_reversal_transaction_ids"), "source_review_invalid"
        )
    )
    if len(correction_sources) != len(set(correction_sources)) or any(
        source_id not in transactions for source_id in correction_sources
    ):
        _fail("source_review_invalid")
    retired_aliases = {
        _source_identity(value, "source_review_invalid")
        for value in _sequence(
            review.get("retired_alias_account_ids"), "source_review_invalid"
        )
    }
    if not retired_aliases.issubset(accounts):
        _fail("source_review_invalid")
    expected_card_balances: dict[tuple[str, str], int] = {}
    for item in _source_rows(
        review.get("expected_card_balances"), "source_review_invalid"
    ):
        key = (
            _source_identity(item.get("source_account_id"), "source_review_invalid"),
            _text(item.get("asset_code"), "source_review_invalid"),
        )
        units = _integer(item.get("natural_units"), "source_review_invalid")
        if key in expected_card_balances:
            _fail("source_review_invalid")
        expected_card_balances[key] = units

    postings_by_transaction: dict[str, list[RawObject]] = {}
    seen_posting_ids: set[str] = set()
    for posting in rows["postings"]:
        transaction_id = _source_identity(
            posting.get("transaction_id"), "source_posting_invalid"
        )
        posting_id = _source_identity(posting.get("id"), "source_posting_invalid")
        if transaction_id not in transactions or posting_id in seen_posting_ids:
            _fail("source_posting_invalid")
        seen_posting_ids.add(posting_id)
        postings_by_transaction.setdefault(transaction_id, []).append(posting)

    posting_rows: list[dict[str, JSONValue]] = []
    mapped_by_transaction: dict[str, tuple[dict[str, JSONValue], ...]] = {}
    correction_by_transaction: dict[str, tuple[dict[str, JSONValue], ...]] = {}
    balance_units: dict[tuple[str, str], int] = {}
    for source_transaction_id, transaction in transactions.items():
        source_postings = sorted(
            postings_by_transaction.get(source_transaction_id, []),
            key=lambda row: (
                _integer(row.get("position"), "source_posting_invalid", minimum=0),
                _source_identity(row.get("id"), "source_posting_invalid").encode(),
            ),
        )
        if len(source_postings) < 2:
            _fail("source_posting_invalid")
        target_transaction_id = _source_uuid(
            "transaction", snapshot_id, source_book_id, source_transaction_id
        )
        mapped: list[dict[str, JSONValue]] = []
        net_by_asset: dict[str, int] = {}
        for position, posting in enumerate(source_postings):
            if (
                _integer(posting.get("position"), "source_posting_invalid", minimum=0)
                != position
            ):
                _fail("source_posting_invalid")
            source_posting_id = _source_identity(
                posting.get("id"), "source_posting_invalid"
            )
            source_account_id = _source_identity(
                posting.get("account_id"), "source_posting_invalid"
            )
            asset_code = _text(posting.get("currency"), "source_posting_invalid")
            if source_account_id not in accounts or asset_code not in asset_scales:
                _fail("source_posting_invalid")
            side, units = _source_exact_amount(
                posting.get("amount"),
                semantics=posting.get("amount_semantics"),
                explicit_side=posting.get("side"),
                ledger_scale=asset_scales[asset_code],
            )
            decision = decisions.get(source_posting_id)
            if decision is not None:
                source_account_id, side = decision
            account = accounts[source_account_id]
            if account.get("currency") != asset_code:
                _fail("source_posting_invalid")
            target_account_id = _source_uuid(
                "account", source_book_id, source_account_id
            )
            signed = units if side == "debit" else -units
            net_by_asset[asset_code] = net_by_asset.get(asset_code, 0) + signed
            balance_key = (target_account_id, asset_code)
            balance_units[balance_key] = balance_units.get(balance_key, 0) + signed
            row: dict[str, JSONValue] = {
                "account_id": target_account_id,
                "asset_code": asset_code,
                "book_id": target_book_id,
                "position": position,
                "posting_id": _source_uuid(
                    "posting",
                    source_book_id,
                    source_transaction_id,
                    source_posting_id,
                ),
                "side": side,
                "transaction_id": target_transaction_id,
                "units": str(units),
            }
            posting_rows.append(row)
            mapped.append(row)
        if any(value != 0 for value in net_by_asset.values()):
            _fail("source_journal_not_balanced")
        mapped_by_transaction[source_transaction_id] = tuple(mapped)

    for source_transaction_id in correction_sources:
        source_rows = mapped_by_transaction[source_transaction_id]
        correction_transaction_id = _source_uuid(
            "transaction",
            snapshot_id,
            source_book_id,
            source_transaction_id,
            "credit-card-semantic-neutralization",
        )
        correction_rows: list[dict[str, JSONValue]] = []
        for source_row in source_rows:
            side = "credit" if source_row["side"] == "debit" else "debit"
            units = int(str(source_row["units"]))
            account_id = str(source_row["account_id"])
            asset_code = str(source_row["asset_code"])
            signed = units if side == "debit" else -units
            key = (account_id, asset_code)
            balance_units[key] = balance_units.get(key, 0) + signed
            correction_rows.append(
                {
                    "account_id": account_id,
                    "asset_code": asset_code,
                    "book_id": target_book_id,
                    "position": int(source_row["position"]),
                    "posting_id": _source_uuid(
                        "posting",
                        source_book_id,
                        source_transaction_id,
                        "reviewed-card-correction",
                        str(source_row["position"]),
                    ),
                    "side": side,
                    "transaction_id": correction_transaction_id,
                    "units": str(units),
                }
            )
        posting_rows.extend(correction_rows)
        correction_by_transaction[source_transaction_id] = tuple(correction_rows)

    card_rows: list[dict[str, JSONValue]] = []
    observed_card_balances: dict[tuple[str, str], int] = {}
    for source_account_id, account in accounts.items():
        subtype = account.get("subtype")
        if subtype not in {"credit_card", "legacy_credit_card"}:
            continue
        asset_code = _text(account.get("currency"), "source_account_invalid")
        target_account_id = _source_uuid("account", source_book_id, source_account_id)
        raw_units = balance_units.get((target_account_id, asset_code), 0)
        account_type = _text(account.get("type"), "source_account_invalid")
        natural_units = (
            -raw_units
            if account_type in {"liability", "equity", "income"}
            else raw_units
        )
        observed_card_balances[(source_account_id, asset_code)] = natural_units
        status = "closed" if source_account_id in retired_aliases else "active"
        if status == "closed" and natural_units != 0:
            _fail("source_alias_balance_invalid")
        card_rows.append(
            {
                "account_id": target_account_id,
                "asset_code": asset_code,
                "book_id": target_book_id,
                "natural_balance_units": str(natural_units),
                "status": status,
            }
        )
    if observed_card_balances != expected_card_balances:
        _fail("source_card_balance_invalid")

    category_versions: dict[str, tuple[str, str]] = {}
    versions_by_source: dict[str, RawObject] = {}
    for version in rows["category_versions"]:
        version_source = _source_identity(
            version.get("category_version_id"), "source_category_invalid"
        )
        category_source = _source_identity(
            version.get("category_id"), "source_category_invalid"
        )
        versions_by_source[version_source] = version
        if category_source in category_versions:
            _fail("source_category_invalid")
        category_versions[category_source] = (
            _source_uuid("category", source_book_id, category_source),
            _source_uuid("category_version", source_book_id, version_source),
        )

    lines_by_transaction: dict[str, list[RawObject]] = {}
    for line in rows["transaction_lines"]:
        transaction_id = _source_identity(
            line.get("transaction_id"), "source_reporting_invalid"
        )
        if transaction_id not in transactions:
            _fail("source_reporting_invalid")
        lines_by_transaction.setdefault(transaction_id, []).append(line)
    reporting_rows: list[dict[str, JSONValue]] = []
    reporting_by_source: dict[str, dict[str, JSONValue]] = {}
    for source_transaction_id, source_lines in lines_by_transaction.items():
        categorized = [
            line
            for line in source_lines
            if line.get("category_id") is not None
            or line.get("category_version_id") is not None
        ]
        if not categorized:
            continue
        if len(categorized) != 1:
            _fail("source_reporting_invalid")
        line = categorized[0]
        category_source = (
            None
            if line.get("category_id") is None
            else _source_identity(line.get("category_id"), "source_reporting_invalid")
        )
        version_source = (
            None
            if line.get("category_version_id") is None
            else _source_identity(
                line.get("category_version_id"), "source_reporting_invalid"
            )
        )
        if category_source is None:
            version = versions_by_source.get(str(version_source))
            if version is None:
                _fail("source_reporting_invalid")
            category_source = _source_identity(
                version.get("category_id"), "source_reporting_invalid"
            )
        target_category = category_versions.get(category_source)
        if target_category is None:
            _fail("source_reporting_invalid")
        category_id, current_version_id = target_category
        catalog_id = current_version_id
        if version_source is not None:
            version = versions_by_source.get(version_source)
            if (
                version is None
                or _source_identity(
                    version.get("category_id"), "source_reporting_invalid"
                )
                != category_source
            ):
                _fail("source_reporting_invalid")
            catalog_id = _source_uuid(
                "category_version", source_book_id, version_source
            )
        asset_code = _text(line.get("currency"), "source_reporting_invalid")
        if asset_code not in asset_scales:
            _fail("source_reporting_invalid")
        _side, units = _source_exact_amount(
            line.get("amount"),
            semantics="legacy_signed",
            explicit_side=None,
            ledger_scale=asset_scales[asset_code],
        )
        source_line_id = _source_identity(
            line.get("line_id"), "source_reporting_invalid"
        )
        target_transaction_id = _source_uuid(
            "transaction", snapshot_id, source_book_id, source_transaction_id
        )
        reporting_row: dict[str, JSONValue] = {
            "asset_code": asset_code,
            "book_id": target_book_id,
            "catalog_id": catalog_id,
            "classification_revision": 1,
            "description_ref": None,
            "dimension": "category",
            "dimension_id": category_id,
            "line_id": _source_uuid(
                "line", source_book_id, source_transaction_id, source_line_id
            ),
            "line_kind": _source_reporting_kind(line.get("line_type")),
            "line_version_id": _source_uuid(
                "line_version",
                source_book_id,
                source_transaction_id,
                source_line_id,
                str(
                    _integer(line.get("version"), "source_reporting_invalid", minimum=1)
                ),
            ),
            "position": 0,
            "source_event_id": _source_uuid(
                "event",
                snapshot_id,
                source_book_id,
                source_transaction_id,
                "reporting.assign",
            ),
            "transaction_id": target_transaction_id,
            "units": str(units),
        }
        reporting_rows.append(reporting_row)
        reporting_by_source[source_transaction_id] = reporting_row

    reversal_links = _source_reversal_links(transactions)
    reversal_semantic_rows = [
        {
            "book_id": target_book_id,
            "original_transaction_id": _source_uuid(
                "transaction", snapshot_id, source_book_id, original_id
            ),
            "reason_code": "import_correction",
            "reversal_transaction_id": _source_uuid(
                "transaction", snapshot_id, source_book_id, reverse_id
            ),
        }
        for reverse_id, original_id in reversal_links.items()
    ]
    reversal_semantic_rows.extend(
        {
            "book_id": target_book_id,
            "original_transaction_id": _source_uuid(
                "transaction", snapshot_id, source_book_id, source_id
            ),
            "reason_code": "import_correction",
            "reversal_transaction_id": _source_uuid(
                "transaction",
                snapshot_id,
                source_book_id,
                source_id,
                "credit-card-semantic-neutralization",
            ),
        }
        for source_id in correction_sources
    )

    description_material: list[tuple[str, bytes]] = []
    for source_transaction_id, transaction in transactions.items():
        ordered_lines = sorted(
            lines_by_transaction.get(source_transaction_id, []),
            key=lambda row: (
                _integer(row.get("position"), "source_description_invalid", minimum=0),
                _source_identity(row.get("line_id"), "source_description_invalid"),
            ),
        )
        plaintext: JSONValue = {
            "line_memos": [
                _source_optional_text(line.get("memo"), "source_description_invalid")
                for line in ordered_lines
            ],
            "purpose": _source_optional_text(
                transaction.get("purpose"), "source_description_invalid"
            ),
            "transaction_memo": _source_optional_text(
                transaction.get("memo"), "source_description_invalid"
            ),
        }
        description_material.append(
            (
                _source_uuid(
                    "description", snapshot_id, source_book_id, source_transaction_id
                ),
                _canonical_json_bytes(plaintext),
            )
        )
    correction_plaintext = _canonical_json_bytes(
        {
            "line_memos": [],
            "purpose": "reviewed_card_direction_correction",
            "transaction_memo": None,
        }
    )
    for source_id in correction_sources:
        description_material.append(
            (
                _source_uuid(
                    "description",
                    snapshot_id,
                    source_book_id,
                    source_id,
                    "credit-card-semantic-neutralization",
                ),
                correction_plaintext,
            )
        )

    pure_fx = _source_pure_fx_transactions(
        transactions=transactions,
        postings_by_transaction=postings_by_transaction,
        accounts=accounts,
        asset_scales=asset_scales,
    )
    command_id = _source_uuid(
        "command",
        snapshot_id,
        target_book_id,
        "full-financial-history-import-v1",
    )
    event_rows: list[dict[str, JSONValue]] = []
    transaction_rows: list[dict[str, JSONValue]] = []
    external_reference_rows: list[dict[str, JSONValue]] = []
    reversal_rows: list[dict[str, JSONValue]] = []
    events_by_source: dict[str, _RawSourceEvent] = {}

    reversal_source_ids = set(reversal_links)
    original_source_ids = sorted(
        (
            source_id
            for source_id in transactions
            if source_id not in reversal_source_ids
        ),
        key=lambda source_id: _source_transaction_sort_key(transactions[source_id]),
    )
    for source_id in original_source_ids:
        transaction = transactions[source_id]
        target_transaction_id = _source_uuid(
            "transaction", snapshot_id, source_book_id, source_id
        )
        event_id = _source_uuid(
            "event", snapshot_id, source_book_id, source_id, "journal.post"
        )
        description_id = _source_uuid(
            "description", snapshot_id, source_book_id, source_id
        )
        transaction_kind = _source_transaction_kind(
            transaction.get("purpose"), pure_fx=source_id in pure_fx
        )
        mapped_postings = mapped_by_transaction[source_id]
        effective_at, _timestamp, _effective_date = _source_timestamp(
            transaction.get("occurred_at"), "source_transaction_invalid"
        )
        reference_value = _opaque_source_reference(
            "transaction", source_book_id, source_id
        )
        payload: dict[str, JSONValue] = {
            "description_ref": description_id,
            "external_references": [
                {
                    "kind": "provider_transaction",
                    "provider_code": "v1_history",
                    "reference": reference_value,
                }
            ],
            "kind": transaction_kind,
            "postings": _event_postings(mapped_postings),
            "transaction_id": target_transaction_id,
        }
        event = _append_source_event(
            event_rows,
            book_id=target_book_id,
            event_id=event_id,
            stream_type="journal_transaction",
            stream_id=target_transaction_id,
            event_type="JournalTransactionPosted",
            payload=payload,
            command_id=command_id,
            causation_event_id=None,
            effective_at=effective_at,
        )
        transaction_rows.append(
            {
                "book_id": target_book_id,
                "description_ref": description_id,
                "effective_at": effective_at,
                "kind": transaction_kind,
                "source_event_id": event_id,
                "source_position": int(event["book_position"]),
                "transaction_id": target_transaction_id,
            }
        )
        external_reference_rows.append(
            {
                "book_id": target_book_id,
                "provider_code": "v1_history",
                "reference_kind": "provider_transaction",
                "reference_value": reference_value,
                "source_event_id": event_id,
                "transaction_id": target_transaction_id,
            }
        )
        events_by_source[source_id] = _RawSourceEvent(
            event_id=event_id,
            event_hash=str(event["event_hash"]),
            transaction_id=target_transaction_id,
            transaction_kind=transaction_kind,
            postings=mapped_postings,
            event_type="JournalTransactionPosted",
        )

    remaining_reversals = set(reversal_links)
    while remaining_reversals:
        available = sorted(
            (
                source_id
                for source_id in remaining_reversals
                if reversal_links[source_id] in events_by_source
            ),
            key=lambda source_id: _source_transaction_sort_key(transactions[source_id]),
        )
        if not available:
            _fail("source_reversal_invalid")
        for source_id in available:
            original_source_id = reversal_links[source_id]
            source_event = events_by_source[original_source_id]
            transaction = transactions[source_id]
            inverse_postings = mapped_by_transaction[source_id]
            if len(source_event.postings) != len(inverse_postings):
                _fail("source_reversal_invalid")
            for original, inverse in zip(
                source_event.postings, inverse_postings, strict=True
            ):
                if (
                    inverse["position"] != original["position"]
                    or inverse["account_id"] != original["account_id"]
                    or inverse["asset_code"] != original["asset_code"]
                    or inverse["units"] != original["units"]
                    or inverse["side"]
                    != ("credit" if original["side"] == "debit" else "debit")
                ):
                    _fail("source_reversal_invalid")
            target_transaction_id = _source_uuid(
                "transaction", snapshot_id, source_book_id, source_id
            )
            event_id = _source_uuid(
                "event", snapshot_id, source_book_id, source_id, "journal.reverse"
            )
            description_id = _source_uuid(
                "description", snapshot_id, source_book_id, source_id
            )
            effective_at, _timestamp, _effective_date = _source_timestamp(
                transaction.get("occurred_at"), "source_transaction_invalid"
            )
            payload = {
                "description_ref": description_id,
                "inverse_postings": _event_postings(inverse_postings),
                "original_event_hash": source_event.event_hash,
                "original_event_id": source_event.event_id,
                "reason_code": "import_correction",
                "reversal_transaction_id": target_transaction_id,
                "reverses_transaction_id": source_event.transaction_id,
            }
            event = _append_source_event(
                event_rows,
                book_id=target_book_id,
                event_id=event_id,
                stream_type="journal_transaction",
                stream_id=target_transaction_id,
                event_type="JournalTransactionReversed",
                payload=payload,
                command_id=command_id,
                causation_event_id=source_event.event_id,
                effective_at=effective_at,
            )
            transaction_rows.append(
                {
                    "book_id": target_book_id,
                    "description_ref": description_id,
                    "effective_at": effective_at,
                    "kind": source_event.transaction_kind,
                    "source_event_id": event_id,
                    "source_position": int(event["book_position"]),
                    "transaction_id": target_transaction_id,
                }
            )
            reversal_rows.append(
                {
                    "book_id": target_book_id,
                    "original_event_hash": source_event.event_hash,
                    "original_event_id": source_event.event_id,
                    "original_transaction_id": source_event.transaction_id,
                    "reason_code": "import_correction",
                    "reversal_transaction_id": target_transaction_id,
                    "source_event_id": event_id,
                }
            )
            events_by_source[source_id] = _RawSourceEvent(
                event_id=event_id,
                event_hash=str(event["event_hash"]),
                transaction_id=target_transaction_id,
                transaction_kind=source_event.transaction_kind,
                postings=inverse_postings,
                event_type="JournalTransactionReversed",
            )
            remaining_reversals.remove(source_id)

    already_reversed_sources = set(reversal_links.values())
    for source_id in sorted(
        correction_sources,
        key=lambda identity: _source_transaction_sort_key(transactions[identity]),
    ):
        source_event = events_by_source.get(source_id)
        inverse_postings = correction_by_transaction.get(source_id)
        if (
            source_event is None
            or inverse_postings is None
            or source_id in already_reversed_sources
        ):
            _fail("source_reversal_invalid")
        target_transaction_id = _source_uuid(
            "transaction",
            snapshot_id,
            source_book_id,
            source_id,
            "credit-card-semantic-neutralization",
        )
        event_id = _source_uuid(
            "event",
            snapshot_id,
            source_book_id,
            source_id,
            "credit-card-semantic-neutralization",
        )
        description_id = _source_uuid(
            "description",
            snapshot_id,
            source_book_id,
            source_id,
            "credit-card-semantic-neutralization",
        )
        effective_at, _timestamp, _effective_date = _source_timestamp(
            transactions[source_id].get("occurred_at"), "source_transaction_invalid"
        )
        payload = {
            "description_ref": description_id,
            "inverse_postings": _event_postings(inverse_postings),
            "original_event_hash": source_event.event_hash,
            "original_event_id": source_event.event_id,
            "reason_code": "import_correction",
            "reversal_transaction_id": target_transaction_id,
            "reverses_transaction_id": source_event.transaction_id,
        }
        event = _append_source_event(
            event_rows,
            book_id=target_book_id,
            event_id=event_id,
            stream_type="journal_transaction",
            stream_id=target_transaction_id,
            event_type="JournalTransactionReversed",
            payload=payload,
            command_id=command_id,
            causation_event_id=source_event.event_id,
            effective_at=effective_at,
        )
        transaction_rows.append(
            {
                "book_id": target_book_id,
                "description_ref": description_id,
                "effective_at": effective_at,
                "kind": source_event.transaction_kind,
                "source_event_id": event_id,
                "source_position": int(event["book_position"]),
                "transaction_id": target_transaction_id,
            }
        )
        reversal_rows.append(
            {
                "book_id": target_book_id,
                "original_event_hash": source_event.event_hash,
                "original_event_id": source_event.event_id,
                "original_transaction_id": source_event.transaction_id,
                "reason_code": "import_correction",
                "reversal_transaction_id": target_transaction_id,
                "source_event_id": event_id,
            }
        )

    for source_id in sorted(
        reporting_by_source,
        key=lambda identity: _source_transaction_sort_key(transactions[identity]),
    ):
        source_event = events_by_source.get(source_id)
        reporting = reporting_by_source[source_id]
        if (
            source_event is None
            or source_event.event_type != "JournalTransactionPosted"
        ):
            _fail("source_reporting_invalid")
        line: dict[str, JSONValue] = {
            key: value
            for key, value in reporting.items()
            if key
            not in {
                "book_id",
                "classification_revision",
                "source_event_id",
                "transaction_id",
            }
        }
        effective_at, _timestamp, _effective_date = _source_timestamp(
            transactions[source_id].get("occurred_at"), "source_transaction_invalid"
        )
        _append_source_event(
            event_rows,
            book_id=target_book_id,
            event_id=str(reporting["source_event_id"]),
            stream_type="reporting_lines",
            stream_id=source_event.transaction_id,
            event_type="ReportingLinesAssigned",
            payload={
                "classification_revision": 1,
                "lines": [line],
                "transaction_id": source_event.transaction_id,
            },
            command_id=command_id,
            causation_event_id=source_event.event_id,
            effective_at=effective_at,
        )

    posting_rows.sort(
        key=lambda row: (str(row["transaction_id"]), int(row["position"]))
    )
    transaction_rows.sort(key=lambda row: str(row["transaction_id"]))
    external_reference_rows.sort(
        key=lambda row: (
            str(row["transaction_id"]),
            str(row["provider_code"]),
            str(row["reference_kind"]),
        )
    )
    reversal_rows.sort(key=lambda row: str(row["reversal_transaction_id"]))
    card_rows.sort(key=lambda row: str(row["account_id"]))
    reporting_rows.sort(
        key=lambda row: (str(row["transaction_id"]), int(row["position"]))
    )
    reversal_semantic_rows.sort(key=lambda row: str(row["reversal_transaction_id"]))
    ordered_balance_units: dict[tuple[str, str], int] = {}
    balance_positions: dict[tuple[str, str], int] = {}
    for event in event_rows:
        payload = _object(event["payload"], "source_event_topology_invalid")
        if event["event_type"] == "JournalTransactionPosted":
            raw_postings = _sequence(
                payload.get("postings"), "source_event_topology_invalid"
            )
        elif event["event_type"] == "JournalTransactionReversed":
            raw_postings = _sequence(
                payload.get("inverse_postings"), "source_event_topology_invalid"
            )
        else:
            continue
        for raw_posting in raw_postings:
            posting = _object(raw_posting, "source_event_topology_invalid")
            account_id = _uuid(
                posting.get("account_id"), "source_event_topology_invalid"
            )
            asset_code = _text(
                posting.get("asset_code"), "source_event_topology_invalid"
            )
            side = _text(posting.get("side"), "source_event_topology_invalid")
            units = _positive_units(
                posting.get("units"), "source_event_topology_invalid"
            )
            key = (account_id, asset_code)
            ordered_balance_units[key] = ordered_balance_units.get(key, 0) + (
                units if side == "debit" else -units
            )
            balance_positions[key] = int(event["book_position"])
    if ordered_balance_units != balance_units:
        _fail("source_event_balance_mismatch")
    balance_semantic_rows = sorted(
        (
            {
                "account_id": account_id,
                "asset_code": asset_code,
                "balance_units": str(units),
                "book_id": target_book_id,
            }
            for (account_id, asset_code), units in balance_units.items()
        ),
        key=lambda row: (str(row["account_id"]), str(row["asset_code"])),
    )
    balance_rows = [
        {
            "account_id": row["account_id"],
            "as_of_position": balance_positions[
                (str(row["account_id"]), str(row["asset_code"]))
            ],
            "asset_code": row["asset_code"],
            "balance_units": row["balance_units"],
            "book_id": row["book_id"],
        }
        for row in balance_semantic_rows
    ]

    reversal_dates_by_source: dict[str, date] = {}
    for reversal_source_id, original_source_id in reversal_links.items():
        _canonical, _timestamp, reversal_date = _source_timestamp(
            transactions[reversal_source_id].get("occurred_at"),
            "source_transaction_invalid",
        )
        if original_source_id in reversal_dates_by_source:
            _fail("source_reversal_invalid")
        reversal_dates_by_source[original_source_id] = reversal_date
    for source_id in correction_sources:
        _canonical, _timestamp, reversal_date = _source_timestamp(
            transactions[source_id].get("occurred_at"),
            "source_transaction_invalid",
        )
        if source_id in reversal_dates_by_source:
            _fail("source_reversal_invalid")
        reversal_dates_by_source[source_id] = reversal_date

    monthly_totals: dict[tuple[str, str, str, str, str], int] = {}
    for source_id, row in reporting_by_source.items():
        _canonical, _timestamp, effective_date = _source_timestamp(
            transactions[source_id].get("occurred_at"),
            "source_transaction_invalid",
        )
        periods_and_signs = [(_month_start(effective_date), 1)]
        reversal_date = reversal_dates_by_source.get(source_id)
        if reversal_date is not None:
            periods_and_signs.append((_month_start(reversal_date), -1))
        for period_start, sign in periods_and_signs:
            key = (
                period_start,
                str(row["dimension_id"]),
                str(row["catalog_id"]),
                str(row["asset_code"]),
                str(row["line_kind"]),
            )
            monthly_totals[key] = monthly_totals.get(key, 0) + sign * int(
                str(row["units"])
            )
    async_rows = sorted(
        (
            {
                "asset_code": asset_code,
                "book_id": target_book_id,
                "category_id": category_id,
                "category_version_id": category_version_id,
                "line_kind": line_kind,
                "period_start": period_start,
                "units": str(units),
            }
            for (
                period_start,
                category_id,
                category_version_id,
                asset_code,
                line_kind,
            ), units in monthly_totals.items()
            if units != 0
        ),
        key=lambda row: (
            str(row["period_start"]),
            str(row["category_id"]),
            str(row["category_version_id"]),
            str(row["asset_code"]),
            str(row["line_kind"]),
        ),
    )
    usdt_rows = sorted(
        (row for row in posting_rows if row["asset_code"] == "USDT"),
        key=lambda row: str(row["posting_id"]),
    )
    transaction_count = len(transaction_rows)
    reversal_count = len(reversal_rows)
    event_count = len(event_rows)
    if (
        transaction_count != len(transactions) + len(correction_sources)
        or reversal_count != len(reversal_links) + len(correction_sources)
        or event_count != transaction_count + len(reporting_rows)
    ):
        _fail("source_event_topology_invalid")
    counts = {
        "accounts": len(accounts),
        "archives": 1,
        "assets": len(asset_scales),
        "async_projection_rows": len(async_rows),
        "categories": len(rows["categories"]),
        "category_versions": len(rows["category_versions"]),
        "credit_card_transactions": 0,
        "descriptions": len(description_material),
        "journal_postings": len(posting_rows),
        "journal_transactions": transaction_count,
        "ledger_events": event_count,
        "quarantine": 0,
        "reporting_lines": len(reporting_rows),
        "reversals": reversal_count,
        "synchronous_projection_applied_events": event_count,
    }
    transaction_hash = _hash_rows(transaction_rows)
    posting_hash = _hash_rows(posting_rows)
    external_reference_hash = _hash_rows(external_reference_rows)
    synchronous_rows = sorted(
        (
            {"event_id": str(row["event_id"]), "projection_version": 1}
            for row in event_rows
        ),
        key=lambda row: str(row["event_id"]),
    )
    hashes = {
        "account_balances_semantic": _hash_rows(balance_semantic_rows),
        "async_projection": _hash_rows(async_rows),
        "balances": _hash_rows(balance_rows),
        "cards": _hash_rows(card_rows),
        "event_order": _event_order_hash(event_rows),
        "event_payloads": _event_payloads_hash(event_rows),
        "events": _hash_rows(event_rows),
        "external_references": external_reference_hash,
        "journal": _combined_hash(
            external_references=external_reference_hash,
            postings=posting_hash,
            transactions=transaction_hash,
        ),
        "journal_postings": posting_hash,
        "journal_transactions": transaction_hash,
        "reporting": _hash_rows(reporting_rows),
        "reversal_semantic": _hash_rows(reversal_semantic_rows),
        "reversals": _hash_rows(reversal_rows),
        "synchronous_projection": _hash_rows(synchronous_rows),
        "usdt_postings": _hash_rows(usdt_rows),
    }
    return SourceLedgerFacts(
        book_id=target_book_id,
        terminal_position=event_count,
        terminal_hash=str(event_rows[-1]["event_hash"]),
        counts=counts,
        hashes=hashes,
        description_ids=tuple(sorted(identity for identity, _ in description_material)),
        description_aggregate_sha256=_description_aggregate(description_material),
    )


def bind_source_reference(
    auxiliary: ReferenceLedgerFacts,
    source: SourceLedgerFacts,
) -> ReferenceLedgerFacts:
    """Overlay source-derived semantics onto plan-derived integrity facts."""

    if (
        type(auxiliary) is not ReferenceLedgerFacts
        or type(source) is not SourceLedgerFacts
    ):
        raise TypeError("source reference arguments are invalid")
    if auxiliary.book_id != source.book_id:
        _fail("source_target_book_mismatch")
    source_integrity_keys = (
        "async_projection",
        "balances",
        "event_order",
        "event_payloads",
        "events",
        "external_references",
        "journal",
        "journal_transactions",
        "reversals",
        "synchronous_projection",
    )
    if (
        auxiliary.terminal_position != source.terminal_position
        or auxiliary.terminal_hash != source.terminal_hash
        or any(
            auxiliary.hashes.get(key) != source.hashes.get(key)
            for key in source_integrity_keys
        )
    ):
        _fail("source_plan_integrity_mismatch")
    counts = dict(auxiliary.counts)
    counts.update(source.counts)
    hashes = dict(auxiliary.hashes)
    hashes.update(source.hashes)
    return ReferenceLedgerFacts(
        book_id=auxiliary.book_id,
        plan_hash=auxiliary.plan_hash,
        terminal_position=source.terminal_position,
        terminal_hash=source.terminal_hash,
        counts=counts,
        hashes=hashes,
        description_ids=source.description_ids,
        description_aggregate_sha256=source.description_aggregate_sha256,
        archive_id=auxiliary.archive_id,
        archive_plaintext_sha256=auxiliary.archive_plaintext_sha256,
        archive_metadata_hash=auxiliary.archive_metadata_hash,
    )


def reduce_canonical_plan(plan: RawObject) -> ReferenceLedgerFacts:
    root = _object(plan, "plan_shape_invalid")
    if set(root) != _EXPECTED_ROOT_KEYS or root.get("contract_version") != 1:
        _fail("plan_shape_invalid")
    book_id = _uuid(root.get("target_book_id"), "plan_shape_invalid")
    source_dump_hash = _sha256(root.get("source_dump_hash"), "plan_shape_invalid")
    manifest_hash = _sha256(root.get("manifest_hash"), "plan_shape_invalid")
    card_review_hash = _sha256(root.get("card_review_hash"), "plan_shape_invalid")
    expected_terminal_hash = _sha256(
        root.get("expected_terminal_hash"), "plan_shape_invalid"
    )
    quarantine_count = _integer(root.get("quarantine_count"), "plan_shape_invalid")
    plan_json = _json_value(dict(root), "plan_shape_invalid")
    if type(plan_json) is not dict:
        _fail("plan_shape_invalid")
    plan_hash = hashlib.sha256(_canonical_json_bytes(plan_json)).hexdigest()

    asset_rows: list[dict[str, JSONValue]] = []
    asset_scales: dict[str, tuple[int, int, int]] = {}
    for raw_asset in _sequence(root.get("assets"), "asset_shape_invalid"):
        asset = _object(raw_asset, "asset_shape_invalid")
        asset_code = _text(asset.get("asset_code"), "asset_shape_invalid")
        if asset_code in asset_scales:
            _fail("asset_identity_duplicate")
        scales = (
            _integer(asset.get("ledger_scale"), "asset_shape_invalid", minimum=0),
            _integer(asset.get("input_scale"), "asset_shape_invalid", minimum=0),
            _integer(asset.get("display_scale"), "asset_shape_invalid", minimum=0),
        )
        if scales[1] > scales[0] or scales[2] > scales[0]:
            _fail("asset_scale_invalid")
        asset_scales[asset_code] = scales
        asset_rows.append(
            {
                "asset_code": asset_code,
                "current_name": _text(asset.get("current_name"), "asset_shape_invalid"),
                "display_scale": scales[2],
                "input_scale": scales[1],
                "kind": _text(asset.get("kind"), "asset_shape_invalid"),
                "ledger_scale": scales[0],
                "status": _text(asset.get("status"), "asset_shape_invalid"),
            }
        )
    if asset_scales.get("USDT") != (8, 6, 6):
        _fail("usdt_scale_mismatch")

    raw_accounts = _sequence(root.get("accounts"), "account_shape_invalid")
    account_plans: dict[str, RawObject] = {}
    account_assets: dict[str, str] = {}
    for raw_account in raw_accounts:
        account = _object(raw_account, "account_shape_invalid")
        account_id = _uuid(account.get("account_id"), "account_shape_invalid")
        asset_code = _text(account.get("asset_code"), "account_shape_invalid")
        if account_id in account_plans or asset_code not in asset_scales:
            _fail("account_identity_or_asset_invalid")
        account_plans[account_id] = account
        account_assets[account_id] = asset_code

    category_rows: list[dict[str, JSONValue]] = []
    category_versions: dict[str, str] = {}
    for raw_category in _sequence(root.get("categories"), "category_shape_invalid"):
        category = _object(raw_category, "category_shape_invalid")
        version = _object(category.get("version"), "category_shape_invalid")
        category_id = _uuid(category.get("category_id"), "category_shape_invalid")
        version_id = _uuid(version.get("category_version_id"), "category_shape_invalid")
        if category_id in category_versions:
            _fail("category_identity_duplicate")
        if (
            _uuid(category.get("current_version_id"), "category_shape_invalid")
            != version_id
        ):
            _fail("category_version_mismatch")
        category_versions[category_id] = version_id
        category_rows.extend(
            (
                {
                    "book_id": book_id,
                    "category_id": category_id,
                    "current_name": _text(
                        category.get("current_name"), "category_shape_invalid"
                    ),
                    "current_version_id": version_id,
                    "parent_category_id": _optional_uuid(
                        category.get("parent_category_id"), "category_shape_invalid"
                    ),
                    "record_type": "category",
                    "status": _text(category.get("status"), "category_shape_invalid"),
                },
                {
                    "book_id": book_id,
                    "category_id": category_id,
                    "category_version_id": version_id,
                    "change_reason_code": _text(
                        version.get("change_reason_code"), "category_shape_invalid"
                    ),
                    "name": _text(version.get("name"), "category_shape_invalid"),
                    "parent_category_id": _optional_uuid(
                        version.get("parent_category_id"), "category_shape_invalid"
                    ),
                    "record_type": "category_version",
                    "status": _text(version.get("status"), "category_shape_invalid"),
                },
            )
        )

    description_material: list[tuple[str, bytes]] = []
    for raw_description in _sequence(
        root.get("descriptions"), "description_shape_invalid"
    ):
        description = _object(raw_description, "description_shape_invalid")
        if description.get("kind") != "transaction_description":
            _fail("description_shape_invalid")
        description_material.append(
            (
                _uuid(description.get("sidecar_id"), "description_shape_invalid"),
                _decode_canonical_bytes(
                    description.get("canonical_plaintext"),
                    "description_shape_invalid",
                ),
            )
        )
    description_ids = tuple(identity for identity, _plaintext in description_material)
    if len(description_ids) != len(set(description_ids)):
        _fail("description_identity_duplicate")
    description_aggregate_sha256 = _description_aggregate(description_material)

    archive = _object(root.get("archive"), "archive_shape_invalid")
    if archive.get("kind") != "import_archive":
        _fail("archive_shape_invalid")
    archive_id = _uuid(archive.get("sidecar_id"), "archive_shape_invalid")
    archive_plaintext = _decode_canonical_bytes(
        archive.get("canonical_plaintext"), "archive_shape_invalid"
    )
    archive_plaintext_sha256 = hashlib.sha256(archive_plaintext).hexdigest()
    record_counts_value = _json_value(
        dict(_object(archive.get("record_counts"), "archive_shape_invalid")),
        "archive_shape_invalid",
    )
    if type(record_counts_value) is not dict:
        _fail("archive_shape_invalid")

    event_rows: list[dict[str, JSONValue]] = []
    transaction_rows: list[dict[str, JSONValue]] = []
    posting_rows: list[dict[str, JSONValue]] = []
    external_reference_rows: list[dict[str, JSONValue]] = []
    reversal_rows: list[dict[str, JSONValue]] = []
    reporting_by_transaction: dict[str, tuple[dict[str, JSONValue], ...]] = {}
    reporting_lines_for_monthly: dict[str, tuple[dict[str, JSONValue], ...]] = {}
    journal_sources_by_event: dict[str, _JournalSource] = {}
    journal_sources_by_transaction: dict[str, _JournalSource] = {}
    reversal_dates: dict[str, date] = {}
    posting_ids: set[str] = set()
    stream_keys: set[tuple[str, str]] = set()
    event_ids: set[str] = set()
    balance_units: dict[tuple[str, str], int] = {}
    balance_positions: dict[tuple[str, str], int] = {}
    previous_hash = _ZERO_HASH

    raw_events = _sequence(root.get("events"), "event_shape_invalid")
    for expected_position, raw_event in enumerate(raw_events, start=1):
        event = _object(raw_event, "event_shape_invalid")
        event_id = _uuid(event.get("event_id"), "event_shape_invalid")
        if event_id in event_ids:
            _fail("event_identity_duplicate")
        event_ids.add(event_id)
        position = _integer(
            event.get("book_position"), "event_shape_invalid", minimum=1
        )
        if position != expected_position:
            _fail("event_order_mismatch")
        if (
            _integer(event.get("expected_stream_version"), "event_shape_invalid") != 0
            or _integer(event.get("stream_version"), "event_shape_invalid") != 1
            or _integer(event.get("event_schema_version"), "event_shape_invalid") != 1
        ):
            _fail("event_version_mismatch")
        stream_type = _text(event.get("stream_type"), "event_shape_invalid")
        stream_id = _uuid(event.get("stream_id"), "event_shape_invalid")
        if (stream_type, stream_id) in stream_keys:
            _fail("event_stream_duplicate")
        stream_keys.add((stream_type, stream_id))
        stored_previous_hash = _sha256(
            event.get("previous_hash"), "event_shape_invalid"
        )
        if stored_previous_hash != previous_hash:
            _fail("event_previous_hash_mismatch")
        effective_at, effective_date = _canonical_timestamp(
            event.get("effective_at"), "event_shape_invalid"
        )
        payload_value = _json_value(event.get("payload"), "event_payload_invalid")
        if type(payload_value) is not dict:
            _fail("event_payload_invalid")
        computed_hash = _event_hash(
            event=event,
            book_id=book_id,
            effective_at=effective_at,
            previous_hash=previous_hash,
            payload=payload_value,
        )
        stored_hash = _sha256(event.get("event_hash"), "event_shape_invalid")
        if computed_hash != stored_hash:
            _fail("event_hash_mismatch")
        event_type = _text(event.get("event_type"), "event_shape_invalid")
        event_rows.append(
            {
                "actor_subject_id": _text(
                    event.get("actor_subject_id"), "event_shape_invalid"
                ),
                "book_id": book_id,
                "book_position": position,
                "causation_event_id": _optional_uuid(
                    event.get("causation_event_id"), "event_shape_invalid"
                ),
                "command_id": _uuid(event.get("command_id"), "event_shape_invalid"),
                "correlation_id": _uuid(
                    event.get("correlation_id"), "event_shape_invalid"
                ),
                "effective_at": effective_at,
                "event_hash": stored_hash,
                "event_id": event_id,
                "event_schema_version": 1,
                "event_type": event_type,
                "payload": payload_value,
                "previous_hash": previous_hash,
                "stream_id": stream_id,
                "stream_type": stream_type,
                "stream_version": 1,
            }
        )

        payload = _object(event.get("payload"), "event_payload_invalid")
        if event_type == "JournalTransactionPosted":
            transaction_id = _uuid(
                payload.get("transaction_id"), "journal_transaction_invalid"
            )
            if (
                stream_type != "journal_transaction"
                or stream_id != transaction_id
                or event.get("causation_event_id") is not None
                or transaction_id in journal_sources_by_transaction
            ):
                _fail("journal_transaction_invalid")
            transaction_kind = _text(payload.get("kind"), "journal_transaction_invalid")
            postings = _posting_rows(
                payload.get("postings"),
                book_id=book_id,
                transaction_id=transaction_id,
                account_assets=account_assets,
                event_position=position,
                posting_ids=posting_ids,
                balance_units=balance_units,
                balance_positions=balance_positions,
            )
            source = _JournalSource(
                event_id=event_id,
                event_hash=stored_hash,
                transaction_id=transaction_id,
                transaction_kind=transaction_kind,
                effective_date=effective_date,
                postings=postings,
                is_reversal=False,
            )
            journal_sources_by_event[event_id] = source
            journal_sources_by_transaction[transaction_id] = source
            transaction_rows.append(
                {
                    "book_id": book_id,
                    "description_ref": _optional_uuid(
                        payload.get("description_ref"), "journal_transaction_invalid"
                    ),
                    "effective_at": effective_at,
                    "kind": transaction_kind,
                    "source_event_id": event_id,
                    "source_position": position,
                    "transaction_id": transaction_id,
                }
            )
            posting_rows.extend(postings)
            for raw_reference in _sequence(
                payload.get("external_references"), "external_reference_invalid"
            ):
                reference = _object(raw_reference, "external_reference_invalid")
                external_reference_rows.append(
                    {
                        "book_id": book_id,
                        "provider_code": _text(
                            reference.get("provider_code"),
                            "external_reference_invalid",
                        ),
                        "reference_kind": _text(
                            reference.get("kind"), "external_reference_invalid"
                        ),
                        "reference_value": _text(
                            reference.get("reference"), "external_reference_invalid"
                        ),
                        "source_event_id": event_id,
                        "transaction_id": transaction_id,
                    }
                )
        elif event_type == "JournalTransactionReversed":
            transaction_id = _uuid(
                payload.get("reversal_transaction_id"), "reversal_invalid"
            )
            original_event_id = _uuid(
                payload.get("original_event_id"), "reversal_invalid"
            )
            source = journal_sources_by_event.get(original_event_id)
            if (
                source is None
                or stream_type != "journal_transaction"
                or stream_id != transaction_id
                or transaction_id in journal_sources_by_transaction
                or _uuid(payload.get("reverses_transaction_id"), "reversal_invalid")
                != source.transaction_id
                or _sha256(payload.get("original_event_hash"), "reversal_invalid")
                != source.event_hash
                or _optional_uuid(event.get("causation_event_id"), "reversal_invalid")
                != source.event_id
            ):
                _fail("reversal_source_mismatch")
            postings = _posting_rows(
                payload.get("inverse_postings"),
                book_id=book_id,
                transaction_id=transaction_id,
                account_assets=account_assets,
                event_position=position,
                posting_ids=posting_ids,
                balance_units=balance_units,
                balance_positions=balance_positions,
            )
            _validate_inverse(source, postings)
            reversed_source = _JournalSource(
                event_id=event_id,
                event_hash=stored_hash,
                transaction_id=transaction_id,
                transaction_kind=source.transaction_kind,
                effective_date=effective_date,
                postings=postings,
                is_reversal=True,
            )
            journal_sources_by_event[event_id] = reversed_source
            journal_sources_by_transaction[transaction_id] = reversed_source
            reversal_dates[source.transaction_id] = effective_date
            transaction_rows.append(
                {
                    "book_id": book_id,
                    "description_ref": _optional_uuid(
                        payload.get("description_ref"), "reversal_invalid"
                    ),
                    "effective_at": effective_at,
                    "kind": source.transaction_kind,
                    "source_event_id": event_id,
                    "source_position": position,
                    "transaction_id": transaction_id,
                }
            )
            posting_rows.extend(postings)
            reversal_rows.append(
                {
                    "book_id": book_id,
                    "original_event_hash": source.event_hash,
                    "original_event_id": source.event_id,
                    "original_transaction_id": source.transaction_id,
                    "reason_code": _text(
                        payload.get("reason_code"), "reversal_invalid"
                    ),
                    "reversal_transaction_id": transaction_id,
                    "source_event_id": event_id,
                }
            )
        elif event_type == "ReportingLinesAssigned":
            transaction_id = _uuid(payload.get("transaction_id"), "reporting_invalid")
            source = journal_sources_by_transaction.get(transaction_id)
            if (
                source is None
                or source.is_reversal
                or stream_type != "reporting_lines"
                or stream_id != transaction_id
                or _integer(
                    payload.get("classification_revision"),
                    "reporting_invalid",
                    minimum=1,
                )
                != 1
                or _optional_uuid(event.get("causation_event_id"), "reporting_invalid")
                != source.event_id
            ):
                _fail("reporting_source_mismatch")
            raw_lines = _sequence(payload.get("lines"), "reporting_invalid")
            lines: list[dict[str, JSONValue]] = []
            allocated_by_asset: dict[str, int] = {}
            debit_by_asset: dict[str, int] = {}
            for posting in source.postings:
                if posting["side"] == "debit":
                    asset_code = str(posting["asset_code"])
                    debit_by_asset[asset_code] = debit_by_asset.get(
                        asset_code, 0
                    ) + int(str(posting["units"]))
            for line_position, raw_line in enumerate(raw_lines):
                line = _object(raw_line, "reporting_invalid")
                if (
                    _integer(line.get("position"), "reporting_invalid", minimum=0)
                    != line_position
                ):
                    _fail("reporting_order_mismatch")
                dimension = _text(line.get("dimension"), "reporting_invalid")
                dimension_id = _optional_uuid(
                    line.get("dimension_id"), "reporting_invalid"
                )
                catalog_id = _uuid(line.get("catalog_id"), "reporting_invalid")
                if (
                    dimension != "category"
                    or dimension_id is None
                    or category_versions.get(dimension_id) != catalog_id
                ):
                    _fail("reporting_category_version_mismatch")
                asset_code = _text(line.get("asset_code"), "reporting_invalid")
                units = _positive_units(line.get("units"), "reporting_invalid")
                allocated_by_asset[asset_code] = (
                    allocated_by_asset.get(asset_code, 0) + units
                )
                lines.append(
                    {
                        "asset_code": asset_code,
                        "book_id": book_id,
                        "catalog_id": catalog_id,
                        "classification_revision": 1,
                        "description_ref": _optional_uuid(
                            line.get("description_ref"), "reporting_invalid"
                        ),
                        "dimension": dimension,
                        "dimension_id": dimension_id,
                        "line_id": _uuid(line.get("line_id"), "reporting_invalid"),
                        "line_kind": _text(line.get("line_kind"), "reporting_invalid"),
                        "line_version_id": _uuid(
                            line.get("line_version_id"), "reporting_invalid"
                        ),
                        "position": line_position,
                        "source_event_id": event_id,
                        "transaction_id": transaction_id,
                        "units": str(units),
                    }
                )
            if any(
                allocated > debit_by_asset.get(asset_code, 0)
                for asset_code, allocated in allocated_by_asset.items()
            ):
                _fail("reporting_allocation_exceeds_transaction")
            reporting_by_transaction[transaction_id] = tuple(lines)
            reporting_lines_for_monthly[transaction_id] = tuple(lines)
        else:
            _fail("event_type_invalid")
        previous_hash = stored_hash

    if previous_hash != expected_terminal_hash:
        _fail("terminal_hash_mismatch")

    account_rows: list[dict[str, JSONValue]] = []
    card_rows: list[dict[str, JSONValue]] = []
    aliases: list[str] = []
    for account_id, account in account_plans.items():
        asset_code = account_assets[account_id]
        account_type = _text(account.get("account_type"), "account_shape_invalid")
        raw_units = balance_units.get((account_id, asset_code), 0)
        natural_units = (
            -raw_units
            if account_type in {"liability", "equity", "income"}
            else raw_units
        )
        if (
            _integer(account.get("expected_natural_units"), "account_shape_invalid")
            != natural_units
        ):
            _fail("account_natural_balance_mismatch")
        close_after_import = _boolean(
            account.get("close_after_import"), "account_shape_invalid"
        )
        final_status = "closed" if close_after_import else "active"
        if close_after_import:
            aliases.append(account_id)
            if natural_units != 0:
                _fail("retired_alias_balance_mismatch")
        account_rows.append(
            {
                "account_id": account_id,
                "account_subtype": (
                    None
                    if account.get("account_subtype") is None
                    else _text(account.get("account_subtype"), "account_shape_invalid")
                ),
                "account_type": account_type,
                "asset_code": asset_code,
                "book_id": book_id,
                "current_name": _text(
                    account.get("current_name"), "account_shape_invalid"
                ),
                "status": final_status,
                "system_role": (
                    None
                    if account.get("system_role") is None
                    else _text(account.get("system_role"), "account_shape_invalid")
                ),
            }
        )
        if account.get("account_subtype") == "credit_card":
            card_rows.append(
                {
                    "account_id": account_id,
                    "asset_code": asset_code,
                    "book_id": book_id,
                    "natural_balance_units": str(natural_units),
                    "status": final_status,
                }
            )
    if len(card_rows) != 5 or len(aliases) != 1:
        _fail("card_account_contract_mismatch")

    balance_rows = [
        {
            "account_id": account_id,
            "as_of_position": balance_positions[(account_id, asset_code)],
            "asset_code": asset_code,
            "balance_units": str(units),
            "book_id": book_id,
        }
        for (account_id, asset_code), units in balance_units.items()
    ]
    reporting_rows = [
        row
        for transaction_id in sorted(reporting_by_transaction)
        for row in reporting_by_transaction[transaction_id]
    ]

    monthly_totals: dict[tuple[str, str, str, str, str], int] = {}
    for transaction_id, lines in reporting_lines_for_monthly.items():
        source = journal_sources_by_transaction[transaction_id]
        periods_and_signs = [(_month_start(source.effective_date), 1)]
        reversed_at = reversal_dates.get(transaction_id)
        if reversed_at is not None:
            periods_and_signs.append((_month_start(reversed_at), -1))
        for period_start, sign in periods_and_signs:
            for line in lines:
                key = (
                    period_start,
                    str(line["dimension_id"]),
                    str(line["catalog_id"]),
                    str(line["asset_code"]),
                    str(line["line_kind"]),
                )
                monthly_totals[key] = monthly_totals.get(key, 0) + sign * int(
                    str(line["units"])
                )
    async_rows = [
        {
            "asset_code": asset_code,
            "book_id": book_id,
            "category_id": category_id,
            "category_version_id": category_version_id,
            "line_kind": line_kind,
            "period_start": period_start,
            "units": str(units),
        }
        for (
            period_start,
            category_id,
            category_version_id,
            asset_code,
            line_kind,
        ), units in monthly_totals.items()
        if units != 0
    ]
    synchronous_rows = [
        {"event_id": event_id, "projection_version": 1} for event_id in event_ids
    ]

    transaction_rows.sort(key=lambda row: str(row["transaction_id"]))
    posting_rows.sort(
        key=lambda row: (str(row["transaction_id"]), int(row["position"]))
    )
    external_reference_rows.sort(
        key=lambda row: (
            str(row["transaction_id"]),
            str(row["provider_code"]),
            str(row["reference_kind"]),
        )
    )
    reversal_rows.sort(key=lambda row: str(row["reversal_transaction_id"]))
    reporting_rows.sort(
        key=lambda row: (str(row["transaction_id"]), int(row["position"]))
    )
    balance_rows.sort(key=lambda row: (str(row["account_id"]), str(row["asset_code"])))
    balance_semantic_rows = [
        {key: value for key, value in row.items() if key != "as_of_position"}
        for row in balance_rows
    ]
    reversal_semantic_rows = [
        {
            "book_id": row["book_id"],
            "original_transaction_id": row["original_transaction_id"],
            "reason_code": row["reason_code"],
            "reversal_transaction_id": row["reversal_transaction_id"],
        }
        for row in reversal_rows
    ]
    asset_rows.sort(key=lambda row: str(row["asset_code"]))
    account_rows.sort(key=lambda row: str(row["account_id"]))
    category_rows.sort(
        key=lambda row: (
            str(row["category_id"]),
            str(row["record_type"]),
            str(row.get("category_version_id", "")),
        )
    )
    card_rows.sort(key=lambda row: str(row["account_id"]))
    async_rows.sort(
        key=lambda row: (
            str(row["period_start"]),
            str(row["category_id"]),
            str(row["category_version_id"]),
            str(row["asset_code"]),
            str(row["line_kind"]),
        )
    )
    synchronous_rows.sort(key=lambda row: str(row["event_id"]))
    usdt_rows = sorted(
        (row for row in posting_rows if row["asset_code"] == "USDT"),
        key=lambda row: str(row["posting_id"]),
    )
    if not usdt_rows:
        _fail("usdt_posting_missing")

    counts = {
        "accounts": len(account_rows),
        "archives": 1,
        "assets": len(asset_rows),
        "async_projection_rows": len(async_rows),
        "categories": len(category_versions),
        "category_versions": len(category_versions),
        "credit_card_transactions": 0,
        "descriptions": len(description_ids),
        "journal_postings": len(posting_rows),
        "journal_transactions": len(transaction_rows),
        "ledger_events": len(event_rows),
        "quarantine": quarantine_count,
        "reporting_lines": len(reporting_rows),
        "reversals": len(reversal_rows),
        "synchronous_projection_applied_events": len(synchronous_rows),
    }
    if any(counts[key] != expected for key, expected in _PINNED_COUNTS.items()):
        _fail("pinned_count_mismatch")

    transaction_hash = _hash_rows(transaction_rows)
    posting_hash = _hash_rows(posting_rows)
    external_reference_hash = _hash_rows(external_reference_rows)
    hashes = {
        "account_balances_semantic": _hash_rows(balance_semantic_rows),
        "accounts": _hash_rows(account_rows),
        "assets": _hash_rows(asset_rows),
        "async_projection": _hash_rows(async_rows),
        "balances": _hash_rows(balance_rows),
        "cards": _hash_rows(card_rows),
        "categories": _hash_rows(category_rows),
        "event_order": _event_order_hash(event_rows),
        "event_payloads": _event_payloads_hash(event_rows),
        "events": _hash_rows(event_rows),
        "external_references": external_reference_hash,
        "journal": _combined_hash(
            external_references=external_reference_hash,
            postings=posting_hash,
            transactions=transaction_hash,
        ),
        "journal_postings": posting_hash,
        "journal_transactions": transaction_hash,
        "reporting": _hash_rows(reporting_rows),
        "reversal_semantic": _hash_rows(reversal_semantic_rows),
        "reversals": _hash_rows(reversal_rows),
        "synchronous_projection": _hash_rows(synchronous_rows),
        "usdt_postings": _hash_rows(usdt_rows),
    }
    archive_metadata_hash = _hash_rows(
        [
            {
                "archive_id": archive_id,
                "archive_plaintext_sha256": archive_plaintext_sha256,
                "card_review_hash": card_review_hash,
                "plan_hash": plan_hash,
                "record_counts": record_counts_value,
                "source_dump_hash": source_dump_hash,
                "source_manifest_hash": manifest_hash,
            }
        ]
    )
    return ReferenceLedgerFacts(
        book_id=book_id,
        plan_hash=plan_hash,
        terminal_position=len(event_rows),
        terminal_hash=expected_terminal_hash,
        counts=counts,
        hashes=hashes,
        description_ids=tuple(sorted(description_ids)),
        description_aggregate_sha256=description_aggregate_sha256,
        archive_id=archive_id,
        archive_plaintext_sha256=archive_plaintext_sha256,
        archive_metadata_hash=archive_metadata_hash,
    )


__all__ = [
    "ReferenceLedgerFacts",
    "ReferenceReductionError",
    "SourceLedgerFacts",
    "bind_source_reference",
    "reduce_canonical_plan",
    "reduce_frozen_source_rows",
]
