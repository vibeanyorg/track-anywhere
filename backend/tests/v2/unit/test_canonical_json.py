from __future__ import annotations

from collections import UserDict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import sys
from uuid import UUID

import pytest

from track_anywhere.serialization.canonical_json import (
    EventHashEnvelope,
    canonical_json_bytes,
    format_utc_microseconds,
)


class ExampleEnum(str, Enum):
    VALUE = "value"


def test_canonical_json_has_fixed_sorting_spacing_unicode_and_scalar_encoding() -> None:
    value = {"z": 1, "a": [True, None, "雪😀\n\x00"]}

    assert canonical_json_bytes(value) == (
        b'{"a":[true,null,"\xe9\x9b\xaa\xf0\x9f\x98\x80\\n\\u0000"],"z":1}'
    )


def test_canonical_json_is_independent_of_mapping_insertion_order() -> None:
    left = {"outer": {"b": 2, "a": 1}, "tail": False}
    right = {"tail": False, "outer": {"a": 1, "b": 2}}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)


def test_canonical_json_intentionally_does_not_normalize_unicode() -> None:
    nfc = {"value": "é"}
    nfd = {"value": "e\u0301"}

    assert canonical_json_bytes(nfc) != canonical_json_bytes(nfd)


def test_canonical_json_accepts_non_monetary_integers_within_512_digits() -> None:
    value = 10**512 - 1

    assert (
        canonical_json_bytes({"value": value})
        == ('{"value":' + str(value) + "}").encode()
    )


@pytest.mark.parametrize("value", [10**512, -(10**512)])
def test_canonical_json_rejects_integers_outside_the_frozen_512_digit_bound(
    value: int,
) -> None:
    with pytest.raises(ValueError) as captured:
        canonical_json_bytes({"value": value})

    assert str(value) not in str(captured.value)


def test_canonical_integer_bytes_do_not_depend_on_python_int_string_limit() -> None:
    value = {"negative": -(10**512 - 1), "positive": 10**512 - 1}
    original_limit = sys.get_int_max_str_digits()
    expected = canonical_json_bytes(value)

    try:
        sys.set_int_max_str_digits(0)
        with_disabled_limit = canonical_json_bytes(value)
    finally:
        sys.set_int_max_str_digits(original_limit)

    assert with_disabled_limit == expected
    assert sys.get_int_max_str_digits() == original_limit


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        Decimal("1.5"),
        UUID("00000000-0000-4000-8000-000000000001"),
        datetime(2026, 7, 13, tzinfo=timezone.utc),
        ExampleEnum.VALUE,
        (1, 2),
        {1, 2},
        UserDict({"a": 1}),
        object(),
    ],
    ids=[
        "float",
        "decimal",
        "uuid",
        "datetime",
        "enum",
        "tuple",
        "set",
        "custom-mapping",
        "object",
    ],
)
def test_canonical_json_rejects_every_non_protocol_type(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes(value)


@pytest.mark.parametrize(
    "value",
    [
        {1: "not-a-string-key"},
        {"nested": [{"ok": 1, 2: "bad"}]},
        {"surrogate": "\ud800"},
        {"surrogate": "\udfff"},
    ],
    ids=["non-string-key", "nested-key", "high-surrogate", "low-surrogate"],
)
def test_canonical_json_rejects_invalid_keys_and_isolated_surrogates(
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes(value)


def test_canonical_json_rejects_cycles_in_lists_and_dicts() -> None:
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    cyclic_dict: dict[str, object] = {}
    cyclic_dict["self"] = cyclic_dict

    with pytest.raises(ValueError, match="cycle"):
        canonical_json_bytes(cyclic_list)
    with pytest.raises(ValueError, match="cycle"):
        canonical_json_bytes(cyclic_dict)


def test_canonical_json_errors_do_not_echo_rejected_input_values() -> None:
    sentinel = "SECRET-SENTINEL-MUST-NOT-LEAK"

    class SecretObject:
        def __repr__(self) -> str:
            return sentinel

    with pytest.raises((TypeError, ValueError)) as captured:
        canonical_json_bytes({"payload": SecretObject()})  # type: ignore[dict-item]

    error = captured.value
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert sentinel not in repr(error.args)
    assert sentinel not in repr(vars(error))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc),
            "2026-07-13T01:02:03.000000Z",
        ),
        (
            datetime(
                2026,
                7,
                13,
                9,
                2,
                3,
                456789,
                tzinfo=timezone(timedelta(hours=8)),
            ),
            "2026-07-13T01:02:03.456789Z",
        ),
        (
            datetime(
                2026,
                7,
                12,
                20,
                32,
                3,
                7,
                tzinfo=timezone(timedelta(hours=-4, minutes=-30)),
            ),
            "2026-07-13T01:02:03.000007Z",
        ),
    ],
)
def test_format_utc_microseconds_is_exact_and_offset_normalized(
    value: datetime,
    expected: str,
) -> None:
    assert format_utc_microseconds(value) == expected


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 7, 13, 1, 2, 3), "2026-07-13T01:02:03Z", object()],
    ids=["naive", "string", "object"],
)
def test_format_utc_microseconds_rejects_naive_and_non_datetimes_safely(
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)) as captured:
        format_utc_microseconds(value)  # type: ignore[arg-type]

    assert repr(value) not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("stream_type", "s" * 33), ("actor_subject_id", "a" * 129)],
)
def test_hash_envelope_string_bounds_match_the_database_schema(
    field: str,
    value: str,
) -> None:
    fields: dict[str, object] = {
        "event_id": UUID("00000000-0000-4000-8000-000000000001"),
        "book_id": UUID("00000000-0000-4000-8000-000000000002"),
        "book_position": 1,
        "global_sequence": 1,
        "stream_type": "journal",
        "stream_id": UUID("00000000-0000-4000-8000-000000000003"),
        "stream_version": 1,
        "event_type": "JournalTransactionPosted",
        "event_schema_version": 1,
        "command_id": UUID("00000000-0000-4000-8000-000000000004"),
        "actor_subject_id": "subject",
        "correlation_id": UUID("00000000-0000-4000-8000-000000000005"),
        "causation_event_id": None,
        "effective_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "recorded_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "previous_hash": bytes(32),
    }
    fields[field] = value

    with pytest.raises(ValueError):
        EventHashEnvelope(**fields)  # type: ignore[arg-type]
