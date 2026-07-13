from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeAlias
from uuid import UUID


JSONScalar: TypeAlias = str | int | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

HASH_DOMAIN_V1 = b"track-anywhere:v2:ledger-event-hash:sha256:v1"
CANONICAL_INTEGER_MAX_DECIMAL_DIGITS = 512

_BIGINT_MAX = 2**63 - 1
_INTEGER_MAX = 2**31 - 1
_SMALLINT_MAX = 2**15 - 1
_STREAM_TYPE_MAX_LENGTH = 32
_EVENT_TYPE_MAX_LENGTH = 64
_ACTOR_SUBJECT_ID_MAX_LENGTH = 128
_CANONICAL_INTEGER_ABS_LIMIT = 10**CANONICAL_INTEGER_MAX_DECIMAL_DIGITS


def _validate_text(value: str) -> None:
    if any("\ud800" <= character <= "\udfff" for character in value):
        raise ValueError("canonical JSON strings must contain valid Unicode scalars")


def _prove_json_value(value: object, active_container_ids: set[int]) -> None:
    value_type = type(value)
    if value_type is bool or value is None:
        return
    if value_type is int:
        if value <= -_CANONICAL_INTEGER_ABS_LIMIT or value >= (
            _CANONICAL_INTEGER_ABS_LIMIT
        ):
            raise ValueError("canonical JSON integer is outside the protocol bound")
        return
    if value_type is str:
        _validate_text(value)
        return
    if value_type is list:
        identity = id(value)
        if identity in active_container_ids:
            raise ValueError("canonical JSON cycle detected")
        active_container_ids.add(identity)
        try:
            for item in value:
                _prove_json_value(item, active_container_ids)
        finally:
            active_container_ids.remove(identity)
        return
    if value_type is dict:
        identity = id(value)
        if identity in active_container_ids:
            raise ValueError("canonical JSON cycle detected")
        active_container_ids.add(identity)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("canonical JSON object keys must be exact strings")
                _validate_text(key)
                _prove_json_value(item, active_container_ids)
        finally:
            active_container_ids.remove(identity)
        return
    raise TypeError("value contains a type outside the canonical JSON protocol")


def canonical_json_bytes(value: JSONValue) -> bytes:
    """Encode a proven exact-builtins JSON value using the frozen V2 protocol."""

    _prove_json_value(value, set())
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return rendered.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        raise ValueError(
            "value cannot be encoded by the canonical JSON protocol"
        ) from None


def format_utc_microseconds(value: datetime) -> str:
    """Render an aware instant as an exact, platform-stable UTC timestamp."""

    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        normalized = value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timestamp must be a valid timezone-aware datetime") from None
    return (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}"
        f".{normalized.microsecond:06d}Z"
    )


def _require_uuid(name: str, value: object, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not UUID:
        raise TypeError(f"{name} must be a UUID")


def _require_bounded_string(name: str, value: object, maximum: int) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > maximum:
        raise ValueError(f"{name} is outside its length bound")


def _require_positive_integer(name: str, value: object, maximum: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 1 or value > maximum:
        raise ValueError(f"{name} is outside its positive integer bound")


@dataclass(frozen=True, slots=True)
class EventHashEnvelope:
    event_id: UUID
    book_id: UUID
    book_position: int
    global_sequence: int
    stream_type: str
    stream_id: UUID
    stream_version: int
    event_type: str
    event_schema_version: int
    command_id: UUID
    actor_subject_id: str
    correlation_id: UUID
    causation_event_id: UUID | None
    effective_at: datetime
    recorded_at: datetime
    previous_hash: bytes

    def __post_init__(self) -> None:
        _require_uuid("event_id", self.event_id)
        _require_uuid("book_id", self.book_id)
        _require_positive_integer("book_position", self.book_position, _BIGINT_MAX)
        _require_positive_integer("global_sequence", self.global_sequence, _BIGINT_MAX)
        _require_bounded_string(
            "stream_type", self.stream_type, _STREAM_TYPE_MAX_LENGTH
        )
        _require_uuid("stream_id", self.stream_id)
        _require_positive_integer("stream_version", self.stream_version, _INTEGER_MAX)
        _require_bounded_string("event_type", self.event_type, _EVENT_TYPE_MAX_LENGTH)
        _require_positive_integer(
            "event_schema_version", self.event_schema_version, _SMALLINT_MAX
        )
        _require_uuid("command_id", self.command_id)
        _require_bounded_string(
            "actor_subject_id",
            self.actor_subject_id,
            _ACTOR_SUBJECT_ID_MAX_LENGTH,
        )
        _require_uuid("correlation_id", self.correlation_id)
        _require_uuid("causation_event_id", self.causation_event_id, optional=True)
        format_utc_microseconds(self.effective_at)
        format_utc_microseconds(self.recorded_at)
        if type(self.previous_hash) is not bytes:
            raise TypeError("previous_hash must be bytes")
        if len(self.previous_hash) != 32:
            raise ValueError("previous_hash must contain exactly 32 bytes")


def _canonical_envelope_value(envelope: EventHashEnvelope) -> dict[str, JSONValue]:
    if type(envelope) is not EventHashEnvelope:
        raise TypeError("envelope must be an EventHashEnvelope")
    return {
        "event_id": str(envelope.event_id),
        "book_id": str(envelope.book_id),
        "book_position": envelope.book_position,
        "stream_type": envelope.stream_type,
        "stream_id": str(envelope.stream_id),
        "stream_version": envelope.stream_version,
        "event_type": envelope.event_type,
        "event_schema_version": envelope.event_schema_version,
        "command_id": str(envelope.command_id),
        "actor_subject_id": envelope.actor_subject_id,
        "correlation_id": str(envelope.correlation_id),
        "causation_event_id": (
            None
            if envelope.causation_event_id is None
            else str(envelope.causation_event_id)
        ),
        "effective_at": format_utc_microseconds(envelope.effective_at),
        "previous_hash": envelope.previous_hash.hex(),
    }


def canonical_hash_parts(
    envelope: EventHashEnvelope,
    stored_payload: dict[str, JSONValue],
) -> tuple[bytes, bytes]:
    """Return the frozen envelope and raw-payload bytes used by the hash."""

    if type(stored_payload) is not dict:
        raise TypeError("stored payload must be an exact dictionary")
    return (
        canonical_json_bytes(_canonical_envelope_value(envelope)),
        canonical_json_bytes(stored_payload),
    )


def event_hash(
    envelope: EventHashEnvelope,
    stored_payload: dict[str, JSONValue],
) -> bytes:
    envelope_bytes, payload_bytes = canonical_hash_parts(envelope, stored_payload)
    return hashlib.sha256(
        HASH_DOMAIN_V1 + b"\0" + envelope_bytes + b"\0" + payload_bytes
    ).digest()
