from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tomllib
import warnings
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from pydantic import AliasChoices, AliasPath, Field, StrictInt, StrictStr, create_model

from track_anywhere.domain.backfill.events import (
    HistoricalCategoryActivityImported,
    HistoricalInvestmentActivityImported,
    HistoricalReportingLineImported,
)
from track_anywhere.domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from track_anywhere.domain.journal.events import (
    FinancialExternalReferenceCorrected,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from track_anywhere.domain.privacy import EventContract, FrozenContract
from track_anywhere.domain.reporting.events import (
    ReportingLinesAssigned,
    ReportingLinesCleared,
)
from track_anywhere.serialization.canonical_json import (
    HASH_DOMAIN_V1,
    EventHashEnvelope,
    canonical_hash_parts,
    event_hash,
)
from track_anywhere.serialization.event_registry import (
    PRODUCTION_EVENT_REGISTRY,
    EventRegistry,
    EventRegistryError,
    StoredEventValidationError,
)
from track_anywhere.serialization.generate_schemas import (
    check_schemas,
    default_schema_directory,
    generate_schemas,
    main as schema_main,
    schema_file_bytes,
)
from track_anywhere.serialization.upcasters import (
    UpcastError,
    UpcasterRegistry,
    UpcastStep,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "event_hash_vectors.json"
HISTORICAL_FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "historical_event_hash_vectors.json"
)
UUIDS = tuple(UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 30))
PRODUCTION_KEYS = {
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
PRODUCTION_MODELS = (
    JournalTransactionPosted,
    JournalTransactionReversed,
    FinancialExternalReferenceCorrected,
    ReportingLinesAssigned,
    ReportingLinesCleared,
    InvestmentLotAcquired,
    InvestmentLotDisposed,
    HistoricalCategoryActivityImported,
    HistoricalInvestmentActivityImported,
    HistoricalReportingLineImported,
)


class FakeEventV1(EventContract):
    event_type: ClassVar[str] = "FakeEvent"
    schema_version: ClassVar[int] = 1

    name: StrictStr


class FakeEventV2(EventContract):
    event_type: ClassVar[str] = "FakeEvent"
    schema_version: ClassVar[int] = 2

    name: StrictStr
    count: StrictInt = Field(gt=0)


class FakeEventV3(EventContract):
    event_type: ClassVar[str] = "FakeEvent"
    schema_version: ClassVar[int] = 3

    label: StrictStr
    count: StrictInt = Field(gt=0)


def _envelope(**changes: object) -> EventHashEnvelope:
    values: dict[str, object] = {
        "event_id": UUIDS[0],
        "book_id": UUIDS[1],
        "book_position": 7,
        "global_sequence": 11,
        "stream_type": "journal",
        "stream_id": UUIDS[2],
        "stream_version": 3,
        "event_type": "JournalTransactionPosted",
        "event_schema_version": 1,
        "command_id": UUIDS[3],
        "actor_subject_id": "subject-123",
        "correlation_id": UUIDS[4],
        "causation_event_id": None,
        "effective_at": datetime(2026, 7, 13, 1, 2, 3, 4, tzinfo=timezone.utc),
        "recorded_at": datetime(2026, 7, 13, 1, 2, 4, 5, tzinfo=timezone.utc),
        "previous_hash": bytes(32),
    }
    values.update(changes)
    return EventHashEnvelope(**values)  # type: ignore[arg-type]


def _fixture_envelope(raw: dict[str, object]) -> EventHashEnvelope:
    return EventHashEnvelope(
        event_id=UUID(str(raw["event_id"])),
        book_id=UUID(str(raw["book_id"])),
        book_position=int(str(raw["book_position"])),
        global_sequence=int(str(raw["global_sequence"])),
        stream_type=str(raw["stream_type"]),
        stream_id=UUID(str(raw["stream_id"])),
        stream_version=int(str(raw["stream_version"])),
        event_type=str(raw["event_type"]),
        event_schema_version=int(str(raw["event_schema_version"])),
        command_id=UUID(str(raw["command_id"])),
        actor_subject_id=str(raw["actor_subject_id"]),
        correlation_id=UUID(str(raw["correlation_id"])),
        causation_event_id=(
            None
            if raw["causation_event_id"] is None
            else UUID(str(raw["causation_event_id"]))
        ),
        effective_at=datetime.fromisoformat(str(raw["effective_at"])),
        recorded_at=datetime.fromisoformat(str(raw["recorded_at"])),
        previous_hash=bytes.fromhex(str(raw["previous_hash"])),
    )


def test_hash_domain_is_frozen() -> None:
    assert HASH_DOMAIN_V1 == b"track-anywhere:v2:ledger-event-hash:sha256:v1"


def test_canonical_hash_parts_include_exactly_the_frozen_hashed_fields() -> None:
    envelope_bytes, payload_bytes = canonical_hash_parts(
        _envelope(), {"optional": None, "units": "123"}
    )

    decoded = json.loads(envelope_bytes)
    assert decoded == {
        "actor_subject_id": "subject-123",
        "book_id": str(UUIDS[1]),
        "book_position": 7,
        "causation_event_id": None,
        "command_id": str(UUIDS[3]),
        "correlation_id": str(UUIDS[4]),
        "effective_at": "2026-07-13T01:02:03.000004Z",
        "event_id": str(UUIDS[0]),
        "event_schema_version": 1,
        "event_type": "JournalTransactionPosted",
        "previous_hash": "0" * 64,
        "stream_id": str(UUIDS[2]),
        "stream_type": "journal",
        "stream_version": 3,
    }
    assert "global_sequence" not in decoded
    assert "recorded_at" not in decoded
    assert payload_bytes == b'{"optional":null,"units":"123"}'


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("event_id", UUIDS[5]),
        ("book_id", UUIDS[6]),
        ("book_position", 8),
        ("stream_type", "reporting"),
        ("stream_id", UUIDS[7]),
        ("stream_version", 4),
        ("event_type", "JournalTransactionReversed"),
        ("event_schema_version", 2),
        ("command_id", UUIDS[8]),
        ("actor_subject_id", "subject-124"),
        ("correlation_id", UUIDS[9]),
        ("causation_event_id", UUIDS[10]),
        (
            "effective_at",
            datetime(2026, 7, 13, 1, 2, 3, 5, tzinfo=timezone.utc),
        ),
        ("previous_hash", bytes.fromhex("00" * 31 + "01")),
    ],
)
def test_mutating_every_hashed_envelope_field_changes_the_hash(
    field: str,
    replacement: object,
) -> None:
    envelope = _envelope()
    payload = {"outer": {"units": "123", "optional": None}}

    assert event_hash(replace(envelope, **{field: replacement}), payload) != event_hash(
        envelope, payload
    )


def test_nested_payload_mutation_and_missing_versus_null_change_the_hash() -> None:
    envelope = _envelope()

    assert event_hash(envelope, {"nested": {"value": "1"}}) != event_hash(
        envelope, {"nested": {"value": "2"}}
    )
    assert event_hash(envelope, {}) != event_hash(envelope, {"optional": None})


def test_global_sequence_and_recorded_at_are_diagnostic_and_excluded() -> None:
    envelope = _envelope()
    changed = replace(
        envelope,
        global_sequence=9_999,
        recorded_at=datetime(2040, 1, 1, tzinfo=timezone.utc),
    )

    assert event_hash(changed, {"value": 1}) == event_hash(envelope, {"value": 1})


def test_equivalent_timestamp_offsets_and_key_orders_hash_identically() -> None:
    utc = _envelope()
    offset = replace(
        utc,
        effective_at=utc.effective_at.astimezone(timezone(timedelta(hours=8))),
    )

    assert event_hash(utc, {"b": 2, "a": 1}) == event_hash(offset, {"a": 1, "b": 2})


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("book_position", [True, 0, 2**63]),
        ("global_sequence", [True, 0, 2**63]),
        ("stream_version", [True, 0, 2**31]),
        ("event_schema_version", [True, 0, 32768]),
    ],
)
def test_envelope_integer_fields_match_postgresql_positive_bounds(
    field: str,
    values: list[object],
) -> None:
    for value in values:
        with pytest.raises((TypeError, ValueError)):
            _envelope(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stream_type", ""),
        ("stream_type", "s" * 33),
        ("event_type", ""),
        ("event_type", "e" * 65),
        ("actor_subject_id", ""),
        ("actor_subject_id", "a" * 129),
        ("previous_hash", b"x" * 31),
        ("previous_hash", b"x" * 33),
        ("previous_hash", bytearray(32)),
        ("event_id", str(UUIDS[0])),
    ],
)
def test_envelope_rejects_wrong_or_unbounded_field_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _envelope(**{field: value})


@pytest.mark.parametrize("field", ["effective_at", "recorded_at"])
def test_envelope_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValueError):
        _envelope(**{field: datetime(2026, 7, 13, 1, 2, 3)})


def test_golden_hash_vectors_freeze_canonical_bytes_and_final_sha256() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())

    assert len(fixture["vectors"]) >= 2
    for vector in fixture["vectors"]:
        envelope = _fixture_envelope(vector["envelope"])
        payload = vector["payload"]
        PRODUCTION_EVENT_REGISTRY.validate_stored(
            envelope.event_type,
            envelope.event_schema_version,
            payload,
        )
        envelope_bytes, payload_bytes = canonical_hash_parts(envelope, payload)

        assert envelope_bytes.hex() == vector["canonical_envelope_hex"]
        assert payload_bytes.hex() == vector["canonical_payload_hex"]
        assert event_hash(envelope, payload).hex() == vector["event_hash_hex"]


def test_each_historical_import_contract_has_a_golden_hash_vector() -> None:
    fixture = json.loads(HISTORICAL_FIXTURE_PATH.read_text())
    expected_event_types = {
        "HistoricalCategoryActivityImported",
        "HistoricalInvestmentActivityImported",
        "HistoricalReportingLineImported",
    }

    assert {
        vector["envelope"]["event_type"] for vector in fixture["vectors"]
    } == expected_event_types
    for vector in fixture["vectors"]:
        envelope = _fixture_envelope(vector["envelope"])
        payload = vector["payload"]
        PRODUCTION_EVENT_REGISTRY.validate_stored(
            envelope.event_type,
            envelope.event_schema_version,
            payload,
        )
        assert event_hash(envelope, payload).hex() == vector["event_hash_hex"]


def test_production_registry_contains_exactly_the_ten_v2_contracts() -> None:
    assert set(PRODUCTION_EVENT_REGISTRY.keys()) == PRODUCTION_KEYS
    for model in PRODUCTION_MODELS:
        key = (model.event_type, model.schema_version)
        assert PRODUCTION_EVENT_REGISTRY.lookup(*key) is model
        assert PRODUCTION_EVENT_REGISTRY.latest_version(model.event_type) == 1


def test_registry_writer_accepts_only_exact_registered_typed_payloads() -> None:
    event = ReportingLinesCleared(transaction_id=UUIDS[0], classification_revision=1)
    dumped = PRODUCTION_EVENT_REGISTRY.dump_registered(event)

    assert dumped == {
        "transaction_id": str(UUIDS[0]),
        "classification_revision": 1,
    }
    with pytest.raises(EventRegistryError):
        PRODUCTION_EVENT_REGISTRY.dump_registered(dumped)

    class UnregisteredChild(ReportingLinesCleared):
        pass

    with pytest.raises(EventRegistryError):
        PRODUCTION_EVENT_REGISTRY.dump_registered(
            UnregisteredChild(transaction_id=UUIDS[0], classification_revision=1)
        )


def test_registry_lookup_and_validation_fail_closed_for_unknown_contracts() -> None:
    with pytest.raises(EventRegistryError):
        PRODUCTION_EVENT_REGISTRY.lookup("UnknownEvent", 1)
    with pytest.raises(EventRegistryError):
        PRODUCTION_EVENT_REGISTRY.latest_version("UnknownEvent")
    with pytest.raises(EventRegistryError):
        PRODUCTION_EVENT_REGISTRY.validate_stored("ReportingLinesCleared", 2, {})


def test_registry_constructor_rejects_duplicate_models_keys_and_filename_collisions() -> (
    None
):
    class DuplicateFakeV1(EventContract):
        event_type: ClassVar[str] = "FakeEvent"
        schema_version: ClassVar[int] = 1

        value: StrictStr

    class CaseEvent(EventContract):
        event_type: ClassVar[str] = "CaseEvent"
        schema_version: ClassVar[int] = 1

        value: StrictStr

    class CASEEVENT(EventContract):
        event_type: ClassVar[str] = "CASEEVENT"
        schema_version: ClassVar[int] = 1

        value: StrictStr

    with pytest.raises(EventRegistryError, match="model"):
        EventRegistry((FakeEventV1, FakeEventV1))
    with pytest.raises(EventRegistryError, match="key"):
        EventRegistry((FakeEventV1, DuplicateFakeV1))
    with pytest.raises(EventRegistryError, match="filename"):
        EventRegistry((CaseEvent, CASEEVENT))


@pytest.mark.parametrize(
    ("event_type", "version"),
    [
        ("notPascal", 1),
        ("Pascal_Event", 1),
        ("A" * 65, 1),
        ("ValidEvent", 0),
        ("ValidEvent", 32768),
        ("ValidEvent", True),
    ],
)
def test_registry_rejects_invalid_event_metadata(
    event_type: str,
    version: object,
) -> None:
    class InvalidMetadata(EventContract):
        value: StrictStr

    InvalidMetadata.event_type = event_type
    InvalidMetadata.schema_version = version  # type: ignore[assignment]

    with pytest.raises(EventRegistryError):
        EventRegistry((InvalidMetadata,))


@pytest.mark.parametrize(
    "field_name",
    [
        "event_id",
        "book_id",
        "book_position",
        "global_sequence",
        "stream_type",
        "stream_id",
        "stream_version",
        "event_type",
        "schema_version",
        "event_schema_version",
        "command_id",
        "actor_subject_id",
        "correlation_id",
        "causation_event_id",
        "effective_at",
        "recorded_at",
        "previous_hash",
        "event_hash",
        "payload",
    ],
)
def test_registry_rejects_every_envelope_only_payload_field(
    field_name: str,
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        model = create_model(
            f"Contains{field_name.title().replace('_', '')}",
            __base__=EventContract,
            **{field_name: (object, ...)},
        )
    if field_name != "event_type":
        model.event_type = "FakeEnvelopeField"
    if field_name != "schema_version":
        model.schema_version = 1

    with pytest.raises(EventRegistryError, match="payload field"):
        EventRegistry((model,))


def test_registry_allows_legitimate_transaction_and_original_event_ids() -> None:
    class LegitimateIdentifiers(EventContract):
        event_type: ClassVar[str] = "LegitimateIdentifiers"
        schema_version: ClassVar[int] = 1

        transaction_id: UUID
        original_event_id: UUID

    registry = EventRegistry((LegitimateIdentifiers,))

    assert registry.lookup("LegitimateIdentifiers", 1) is LegitimateIdentifiers


def test_registry_rejects_reserved_validation_serialization_and_nested_aliases() -> (
    None
):
    class AliasEvent(EventContract):
        event_type: ClassVar[str] = "AliasEvent"
        schema_version: ClassVar[int] = 1

        safe_value: StrictStr = Field(alias="event_id")

    class ValidationAliasEvent(EventContract):
        event_type: ClassVar[str] = "ValidationAliasEvent"
        schema_version: ClassVar[int] = 1

        safe_value: StrictStr = Field(validation_alias="event_id")

    class SerializationAliasEvent(EventContract):
        event_type: ClassVar[str] = "SerializationAliasEvent"
        schema_version: ClassVar[int] = 1

        safe_value: StrictStr = Field(serialization_alias="event_id")

    class NestedEnvelopeFact(FrozenContract):
        event_id: UUID

    class NestedAliasEvent(EventContract):
        event_type: ClassVar[str] = "NestedAliasEvent"
        schema_version: ClassVar[int] = 1

        nested: NestedEnvelopeFact

    for model in (
        AliasEvent,
        ValidationAliasEvent,
        SerializationAliasEvent,
        NestedAliasEvent,
    ):
        with pytest.raises(EventRegistryError, match="payload field"):
            EventRegistry((model,))


def test_registry_rejects_reserved_segments_hidden_in_complex_validation_aliases() -> (
    None
):
    class AliasChoicesEvent(EventContract):
        event_type: ClassVar[str] = "AliasChoicesEvent"
        schema_version: ClassVar[int] = 1

        safe_value: StrictStr = Field(
            validation_alias=AliasChoices("value", "event_id")
        )

    class AliasPathEvent(EventContract):
        event_type: ClassVar[str] = "AliasPathEvent"
        schema_version: ClassVar[int] = 1

        safe_value: StrictStr = Field(validation_alias=AliasPath("wrapper", "event_id"))

    for model in (AliasChoicesEvent, AliasPathEvent):
        with pytest.raises(EventRegistryError, match="payload field"):
            EventRegistry((model,))


def test_registry_recursively_checks_nested_model_fields_through_type_wrappers() -> (
    None
):
    class NestedAliasFact(FrozenContract):
        safe_value: StrictStr = Field(
            validation_alias=AliasChoices("value", "event_id")
        )

    class LegitimateNestedFact(FrozenContract):
        original_event_id: UUID

    class NestedAliasContainerEvent(EventContract):
        event_type: ClassVar[str] = "NestedAliasContainerEvent"
        schema_version: ClassVar[int] = 1

        nested: dict[str, list[NestedAliasFact | LegitimateNestedFact | None]]

    with pytest.raises(EventRegistryError, match="payload field"):
        EventRegistry((NestedAliasContainerEvent,))


def test_registry_model_field_recursion_is_cycle_safe_and_exact() -> None:
    class RecursiveFact(FrozenContract):
        original_event_id: UUID
        children: tuple[RecursiveFact, ...] = ()

    class RecursiveEvent(EventContract):
        event_type: ClassVar[str] = "RecursiveEvent"
        schema_version: ClassVar[int] = 1

        root: RecursiveFact

    registry = EventRegistry((RecursiveEvent,))

    assert registry.lookup("RecursiveEvent", 1) is RecursiveEvent


def test_registry_fails_closed_if_registered_class_metadata_drifts() -> None:
    class OriginalEvent(EventContract):
        event_type: ClassVar[str] = "OriginalEvent"
        schema_version: ClassVar[int] = 1

        value: StrictStr

    registry = EventRegistry((OriginalEvent,))
    payload = OriginalEvent(value="frozen")
    original_event_type = OriginalEvent.event_type
    original_schema_version = OriginalEvent.schema_version
    failures: list[str] = []
    try:
        OriginalEvent.event_type = "ChangedEvent"
        OriginalEvent.schema_version = 2
        operations = {
            "registrations": lambda: registry.registrations(),
            "keys": registry.keys,
            "models": registry.models,
            "lookup": lambda: registry.lookup("OriginalEvent", 1),
            "latest_version": lambda: registry.latest_version("OriginalEvent"),
            "dump": lambda: registry.dump_registered(payload),
            "validate": lambda: registry.validate_stored(
                "OriginalEvent", 1, {"value": "frozen"}
            ),
            "schemas": lambda: schema_file_bytes(registry),
        }
        for name, operation in operations.items():
            try:
                operation()
            except EventRegistryError:
                continue
            except Exception as error:
                failures.append(f"{name}:{type(error).__name__}")
            else:
                failures.append(f"{name}:accepted")
    finally:
        OriginalEvent.event_type = original_event_type
        OriginalEvent.schema_version = original_schema_version

    assert failures == []
    assert registry.registrations() == ((("OriginalEvent", 1), OriginalEvent),)


@pytest.mark.parametrize(
    "raw",
    [
        {
            "transaction_id": str(UUIDS[0]),
            "classification_revision": "SECRET-SENTINEL-MUST-NOT-LEAK",
        },
        {
            "transaction_id": str(UUIDS[0]),
            "classification_revision": 1,
            "SECRET-SENTINEL-MUST-NOT-LEAK": "SECRET-VALUE-MUST-NOT-LEAK",
        },
        {
            "transaction_id": str(UUIDS[0]),
            "classification_revision": 1.5,
        },
    ],
)
def test_registry_validation_errors_never_leak_payload_values_or_unknown_keys(
    raw: dict[str, object],
) -> None:
    with pytest.raises(StoredEventValidationError) as captured:
        PRODUCTION_EVENT_REGISTRY.validate_stored("ReportingLinesCleared", 1, raw)

    error = captured.value
    rendered = " ".join((str(error), repr(error), repr(error.args), repr(vars(error))))
    assert "SECRET-SENTINEL-MUST-NOT-LEAK" not in rendered
    assert "SECRET-VALUE-MUST-NOT-LEAK" not in rendered
    assert "ReportingLinesCleared.v1" in str(error)
    assert error.issues
    assert all(issue.error_type for issue in error.issues)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    "unknown_key",
    ["units", "posting_id", "SECRET-WRONG-PATH-KEY-MUST-NOT-LEAK"],
)
def test_registry_never_exposes_unknown_keys_even_when_names_exist_elsewhere(
    unknown_key: str,
) -> None:
    raw = {
        "transaction_id": str(UUIDS[0]),
        "kind": "standard",
        "postings": [
            {
                "posting_id": str(UUIDS[1]),
                "position": 0,
                "account_id": str(UUIDS[2]),
                "asset_code": "CNY",
                "side": "debit",
                "units": "1",
            },
            {
                "posting_id": str(UUIDS[3]),
                "position": 1,
                "account_id": str(UUIDS[4]),
                "asset_code": "CNY",
                "side": "credit",
                "units": "1",
            },
        ],
        unknown_key: "SECRET-WRONG-PATH-VALUE-MUST-NOT-LEAK",
    }

    with pytest.raises(StoredEventValidationError) as captured:
        PRODUCTION_EVENT_REGISTRY.validate_stored("JournalTransactionPosted", 1, raw)

    error = captured.value
    rendered = " ".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            repr(vars(error)),
            repr(error.issues),
        )
    )
    assert unknown_key not in rendered
    assert "SECRET-WRONG-PATH-VALUE-MUST-NOT-LEAK" not in rendered
    assert {
        segment
        for issue in error.issues
        for segment in issue.location
        if isinstance(segment, str)
    } <= {"<field>"}
    assert error.__cause__ is None
    assert error.__context__ is None


def test_historical_hash_uses_raw_stored_payload_before_default_insertion() -> None:
    raw = {
        "transaction_id": str(UUIDS[0]),
        "kind": "standard",
        "postings": [
            {
                "posting_id": str(UUIDS[1]),
                "position": 0,
                "account_id": str(UUIDS[2]),
                "asset_code": "CNY",
                "side": "debit",
                "units": "1",
            },
            {
                "posting_id": str(UUIDS[3]),
                "position": 1,
                "account_id": str(UUIDS[4]),
                "asset_code": "CNY",
                "side": "credit",
                "units": "1",
            },
        ],
    }
    before_validation = event_hash(_envelope(), raw)

    typed = PRODUCTION_EVENT_REGISTRY.validate_stored(
        "JournalTransactionPosted", 1, raw
    )
    redumped_with_defaults = PRODUCTION_EVENT_REGISTRY.dump_registered(typed)

    assert "description_ref" not in raw
    assert redumped_with_defaults["description_ref"] is None
    assert before_validation == event_hash(_envelope(), raw)
    assert before_validation != event_hash(_envelope(), redumped_with_defaults)


def _fake_registry() -> EventRegistry:
    return EventRegistry((FakeEventV1, FakeEventV2, FakeEventV3))


def _fake_steps() -> tuple[UpcastStep, UpcastStep]:
    def v1_to_v2(payload: dict[str, object]) -> dict[str, object]:
        return {"name": payload["name"], "count": 1}

    def v2_to_v3(payload: dict[str, object]) -> dict[str, object]:
        return {"label": payload["name"], "count": payload["count"]}

    return (
        UpcastStep("FakeEvent", 1, 2, v1_to_v2),
        UpcastStep("FakeEvent", 2, 3, v2_to_v3),
    )


def test_upcasting_is_pure_repeatable_validated_and_returns_latest_type() -> None:
    raw = {"name": "legacy"}
    original = copy.deepcopy(raw)
    upcasters = UpcasterRegistry(_fake_registry(), _fake_steps())

    first = upcasters.upcast_to_latest("FakeEvent", 1, raw)
    second = upcasters.upcast_to_latest("FakeEvent", 1, raw)

    assert raw == original
    assert first == second == FakeEventV3(label="legacy", count=1)
    assert type(first) is FakeEventV3


def test_upcast_preserves_the_pre_upcast_stored_v1_hash() -> None:
    raw = {"name": "legacy"}
    envelope = _envelope(event_type="FakeEvent", event_schema_version=1)
    stored_hash = event_hash(envelope, raw)

    latest = UpcasterRegistry(_fake_registry(), _fake_steps()).upcast_to_latest(
        "FakeEvent", 1, raw
    )

    assert event_hash(envelope, raw) == stored_hash
    assert event_hash(envelope, latest.model_dump(mode="json")) != stored_hash


def test_upcaster_registry_rejects_duplicate_nonadjacent_unknown_and_missing_steps() -> (
    None
):
    first, second = _fake_steps()

    with pytest.raises(UpcastError, match="duplicate"):
        UpcasterRegistry(_fake_registry(), (first, first, second))
    with pytest.raises(UpcastError, match="adjacent"):
        UpcasterRegistry(
            _fake_registry(),
            (UpcastStep("FakeEvent", 1, 3, lambda payload: payload),),
        )
    with pytest.raises(UpcastError, match="unknown"):
        UpcasterRegistry(
            _fake_registry(),
            (UpcastStep("FakeEvent", 3, 4, lambda payload: payload),),
        )
    with pytest.raises(UpcastError, match="missing"):
        UpcasterRegistry(_fake_registry(), (first,))


def test_upcasters_reject_invalid_return_mutation_nondeterminism_and_bad_targets() -> (
    None
):
    first, second = _fake_steps()

    def invalid_return(payload: dict[str, object]) -> object:
        return [payload]

    def mutates(payload: dict[str, object]) -> dict[str, object]:
        payload["changed"] = True
        return {"name": "legacy", "count": 1}

    call_count = 0

    def nondeterministic(payload: dict[str, object]) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {"name": payload["name"], "count": call_count}

    def invalid_target(payload: dict[str, object]) -> dict[str, object]:
        return {"name": payload["name"], "count": 0}

    cases = (
        (UpcastStep("FakeEvent", 1, 2, invalid_return), "mapping"),
        (UpcastStep("FakeEvent", 1, 2, mutates), "mutate"),
        (UpcastStep("FakeEvent", 1, 2, nondeterministic), "deterministic"),
        (UpcastStep("FakeEvent", 1, 2, invalid_target), "validation"),
    )
    for broken, message in cases:
        registry = UpcasterRegistry(_fake_registry(), (broken, second))
        with pytest.raises(UpcastError, match=message):
            registry.upcast_to_latest("FakeEvent", 1, {"name": "legacy"})


def test_upcaster_transform_exceptions_never_retain_secret_context() -> None:
    sentinel = "SECRET-UPCAST-PAYLOAD-MUST-NOT-LEAK"
    _, second = _fake_steps()

    def raises_with_payload(payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError(f"{sentinel}:{payload!r}")

    upcasters = UpcasterRegistry(
        _fake_registry(),
        (UpcastStep("FakeEvent", 1, 2, raises_with_payload), second),
    )

    with pytest.raises(UpcastError) as captured:
        upcasters.upcast_to_latest("FakeEvent", 1, {"name": sentinel})

    error = captured.value
    rendered = " ".join((str(error), repr(error), repr(error.args), repr(vars(error))))
    assert sentinel not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_production_schema_bytes_are_exact_deterministic_and_metadata_only() -> None:
    expected_filenames = {
        f"{event_type}.v{version}.json" for event_type, version in PRODUCTION_KEYS
    }
    normal = schema_file_bytes(PRODUCTION_EVENT_REGISTRY)
    shuffled = schema_file_bytes(EventRegistry(tuple(reversed(PRODUCTION_MODELS))))

    assert normal == shuffled
    assert set(normal) == expected_filenames
    for filename, raw in normal.items():
        assert raw.endswith(b"\n")
        assert not raw.endswith(b"\n\n")
        decoded = json.loads(raw)
        assert decoded["$id"].endswith(filename)
        assert decoded["x-event-type"] in filename
        assert decoded["x-schema-version"] == 1
        assert "event_type" not in decoded.get("properties", {})
        assert "schema_version" not in decoded.get("properties", {})
        assert (
            raw
            == (
                json.dumps(decoded, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
            ).encode()
        )


def test_schema_generation_and_check_compare_exact_names_and_bytes_without_writing(
    tmp_path: Path,
) -> None:
    generate_schemas(tmp_path)
    assert check_schemas(tmp_path).ok
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    changed = next(iter(tmp_path.iterdir()))
    changed.write_bytes(b"changed\n")
    (tmp_path / "unexpected.json").write_text("{}\n")
    after_corruption = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = check_schemas(tmp_path)

    assert not result.ok
    assert changed.name in result.changed
    assert result.unexpected == ("unexpected.json",)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == (
        after_corruption
    )
    assert before != after_corruption


def test_schema_writer_never_deletes_unmanaged_entries_and_fails_before_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unmanaged = tmp_path / "keep.txt"
    unmanaged.write_text("must survive exactly")
    before = unmanaged.read_bytes()

    with pytest.raises(ValueError):
        generate_schemas(tmp_path)

    assert unmanaged.read_bytes() == before
    assert {path.name for path in tmp_path.iterdir()} == {"keep.txt"}
    assert check_schemas(tmp_path).unexpected == ("keep.txt",)
    assert schema_main(["--output-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "keep.txt" not in captured.out
    assert "keep.txt" not in captured.err


def test_schema_writer_refuses_a_symlink_output_directory(tmp_path: Path) -> None:
    target = tmp_path / "real-output"
    target.mkdir()
    output_link = tmp_path / "linked-output"
    output_link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError):
        generate_schemas(output_link)

    assert list(target.iterdir()) == []
    assert not check_schemas(output_link).ok


def test_schema_writer_refuses_expected_symlink_without_touching_victim(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "schemas"
    output_dir.mkdir()
    filename = next(iter(schema_file_bytes()))
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"external-victim-must-not-change")
    expected_link = output_dir / filename
    expected_link.symlink_to(victim)
    before = victim.read_bytes()

    with pytest.raises(ValueError):
        generate_schemas(output_dir)

    assert victim.read_bytes() == before
    assert {path.name for path in output_dir.iterdir()} == {filename}


def test_schema_check_treats_symlinks_and_directories_as_non_regular_entries(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "schemas"
    generate_schemas(output_dir)
    expected = schema_file_bytes()
    filename = next(iter(expected))
    schema_path = output_dir / filename
    schema_path.unlink()
    victim = tmp_path / "matching-victim.json"
    victim.write_bytes(expected[filename])
    schema_path.symlink_to(victim)
    unexpected_directory = output_dir / "unexpected-directory"
    unexpected_directory.mkdir()
    before = victim.read_bytes()

    result = check_schemas(output_dir)

    assert not result.ok
    assert filename in result.changed
    assert result.unexpected == ("unexpected-directory",)
    assert victim.read_bytes() == before
    assert unexpected_directory.is_dir()


def test_schema_check_marks_expected_fifo_changed_without_blocking(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "schemas"
    generate_schemas(output_dir)
    filename = next(iter(schema_file_bytes()))
    schema_path = output_dir / filename
    schema_path.unlink()
    os.mkfifo(schema_path)
    check_script = """
import sys
from pathlib import Path

from track_anywhere.serialization.generate_schemas import check_schemas

result = check_schemas(Path(sys.argv[1]))
raise SystemExit(0 if sys.argv[2] in result.changed else 3)
"""

    completed = subprocess.run(
        [sys.executable, "-c", check_script, str(output_dir), filename],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_schema_check_reports_missing_and_cli_check_never_writes(
    tmp_path: Path,
) -> None:
    assert schema_main(["--output-dir", str(tmp_path)]) == 0
    removed = next(iter(tmp_path.iterdir()))
    removed.unlink()
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert schema_main(["--check", "--output-dir", str(tmp_path)]) == 1
    assert removed.name in check_schemas(tmp_path).missing
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_committed_production_schemas_are_current() -> None:
    assert check_schemas(default_schema_directory()).ok


def test_root_schema_check_works_without_pythonpath_in_the_project_venv() -> None:
    repository_root = Path(__file__).parents[4]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "track_anywhere.serialization.generate_schemas",
            "--check",
        ],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_setuptools_package_data_declares_all_committed_event_schemas() -> None:
    repository_root = Path(__file__).parents[4]
    configuration = tomllib.loads((repository_root / "pyproject.toml").read_text())

    assert configuration["tool"]["setuptools"]["package-data"][
        "track_anywhere.serialization"
    ] == ["schemas/*.json"]
