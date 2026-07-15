from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeAlias, get_args

from pydantic import AliasChoices, AliasPath, BaseModel, ValidationError

from ..domain.backfill.events import (
    HistoricalCategoryActivityImported,
    HistoricalInvestmentActivityImported,
    HistoricalReportingLineImported,
)
from ..domain.investments.events import (
    InvestmentLotAcquired,
    InvestmentLotDisposed,
)
from ..domain.credit_cards.events import CreditCardTransactionRecorded
from ..domain.journal.events import (
    FinancialExternalReferenceCorrected,
    JournalTransactionPosted,
    JournalTransactionReversed,
)
from ..domain.privacy import EventContract
from ..domain.reporting.events import ReportingLinesAssigned, ReportingLinesCleared
from .canonical_json import JSONValue, canonical_json_bytes


EventModel: TypeAlias = type[EventContract]
EventKey: TypeAlias = tuple[str, int]

_EVENT_TYPE_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]{0,63}$")
_SAFE_ERROR_TYPE_PATTERN = re.compile(r"^[a-z0-9_.-]{1,80}$")
_ENVELOPE_ONLY_FIELDS = frozenset(
    {
        "actor_subject_id",
        "book_id",
        "book_position",
        "causation_event_id",
        "command_id",
        "correlation_id",
        "effective_at",
        "event_hash",
        "event_id",
        "event_schema_version",
        "event_type",
        "global_sequence",
        "payload",
        "previous_hash",
        "recorded_at",
        "schema_version",
        "stream_id",
        "stream_type",
        "stream_version",
    }
)


class EventRegistryError(ValueError):
    """A safe, fail-closed registry configuration or lookup error."""


@dataclass(frozen=True, slots=True)
class StoredValidationIssue:
    location: tuple[str | int, ...]
    error_type: str


class StoredEventValidationError(ValueError):
    """Public validation failure containing no stored payload values."""

    def __init__(
        self,
        event_type: str,
        schema_version: int,
        issues: tuple[StoredValidationIssue, ...],
    ) -> None:
        self.event_type = event_type
        self.schema_version = schema_version
        self.issues = issues
        rendered_issues = ", ".join(
            f"{'.'.join(str(item) for item in issue.location)}:{issue.error_type}"
            for issue in issues
        )
        super().__init__(
            f"stored event {event_type}.v{schema_version} failed validation"
            + (f" ({rendered_issues})" if rendered_issues else "")
        )


def _schema_filename(key: EventKey) -> str:
    return f"{key[0]}.v{key[1]}.json"


def _schema_property_names(value: object) -> frozenset[str]:
    names: set[str] = set()

    def visit(nested: object) -> None:
        if type(nested) is dict:
            properties = nested.get("properties")
            if type(properties) is dict:
                names.update(key for key in properties if type(key) is str)
            for child in nested.values():
                visit(child)
        elif type(nested) is list:
            for child in nested:
                visit(child)

    visit(value)
    return frozenset(names)


def _reserved_schema_fields(model: EventModel) -> frozenset[str]:
    schema_failed = False
    validation_schema: object = {}
    serialization_schema: object = {}
    try:
        validation_schema = model.model_json_schema(mode="validation")
        serialization_schema = model.model_json_schema(mode="serialization")
    except Exception:
        schema_failed = True
    if schema_failed:
        raise EventRegistryError("event contract schema inspection failed")
    return _ENVELOPE_ONLY_FIELDS.intersection(
        _schema_property_names(validation_schema)
        | _schema_property_names(serialization_schema)
    )


def _alias_string_segments(alias: object) -> frozenset[str]:
    if type(alias) is str:
        return frozenset((alias,))
    if isinstance(alias, AliasChoices):
        return frozenset(
            segment
            for choice in alias.choices
            for segment in _alias_string_segments(choice)
        )
    if isinstance(alias, AliasPath):
        return frozenset(segment for segment in alias.path if type(segment) is str)
    return frozenset()


def _reserved_model_fields(model: EventModel) -> frozenset[str]:
    field_names: set[str] = set()
    visited: set[type[BaseModel]] = set()

    def visit_annotation(annotation: object) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            visit_model(annotation)
            return
        for argument in get_args(annotation):
            visit_annotation(argument)

    def visit_model(nested_model: type[BaseModel]) -> None:
        if nested_model in visited:
            return
        visited.add(nested_model)
        for field_name, field in nested_model.model_fields.items():
            field_names.add(field_name)
            field_names.update(_alias_string_segments(field.alias))
            field_names.update(_alias_string_segments(field.validation_alias))
            field_names.update(_alias_string_segments(field.serialization_alias))
            visit_annotation(field.annotation)

    visit_model(model)
    return _ENVELOPE_ONLY_FIELDS.intersection(field_names)


def _safe_issues(
    errors: list[dict[str, object]],
) -> tuple[StoredValidationIssue, ...]:
    issues: list[StoredValidationIssue] = []
    for error in errors:
        raw_location = error.get("loc", ())
        if not isinstance(raw_location, tuple):
            raw_location = ()
        location: list[str | int] = []
        for item in raw_location:
            if type(item) is int and item >= 0:
                location.append(item)
            else:
                location.append("<field>")
        raw_error_type = error.get("type")
        error_type = (
            raw_error_type
            if type(raw_error_type) is str
            and _SAFE_ERROR_TYPE_PATTERN.fullmatch(raw_error_type)
            else "validation_error"
        )
        issues.append(StoredValidationIssue(tuple(location), error_type))
    return tuple(issues)


class EventRegistry:
    def __init__(self, models: Sequence[EventModel]) -> None:
        registrations = tuple(models)
        by_key: dict[EventKey, EventModel] = {}
        by_model: dict[EventModel, EventKey] = {}
        filenames: dict[str, EventKey] = {}

        for model in registrations:
            if not isinstance(model, type) or not issubclass(model, EventContract):
                raise EventRegistryError(
                    "registry model must be an EventContract class"
                )
            if model is EventContract:
                raise EventRegistryError(
                    "base EventContract is not a registerable model"
                )
            if model in by_model:
                raise EventRegistryError("duplicate registry model class")

            forbidden = _reserved_model_fields(model)
            if not forbidden:
                forbidden = _reserved_schema_fields(model)
            if forbidden:
                raise EventRegistryError(
                    "event contract contains an envelope-only payload field"
                )

            event_type = getattr(model, "event_type", None)
            schema_version = getattr(model, "schema_version", None)
            if (
                type(event_type) is not str
                or _EVENT_TYPE_PATTERN.fullmatch(event_type) is None
            ):
                raise EventRegistryError("event type must be bounded PascalCase")
            if (
                type(schema_version) is not int
                or schema_version < 1
                or schema_version > 32767
            ):
                raise EventRegistryError("schema version must be a positive SMALLINT")

            key = (event_type, schema_version)
            if key in by_key:
                raise EventRegistryError("duplicate event registry key")
            filename_key = _schema_filename(key).casefold()
            if filename_key in filenames:
                raise EventRegistryError("case-folded schema filename collision")
            by_key[key] = model
            by_model[model] = key
            filenames[filename_key] = key

        self._by_key = by_key
        self._by_model = by_model
        self._registrations = tuple(sorted(by_key.items()))

    def _assert_live_metadata(self) -> None:
        for key, model in self._registrations:
            live_event_type = getattr(model, "event_type", None)
            live_schema_version = getattr(model, "schema_version", None)
            if (
                type(live_event_type) is not str
                or type(live_schema_version) is not int
                or (live_event_type, live_schema_version) != key
            ):
                raise EventRegistryError("registered event metadata drift detected")

    def registrations(self) -> tuple[tuple[EventKey, EventModel], ...]:
        self._assert_live_metadata()
        return self._registrations

    def keys(self) -> tuple[EventKey, ...]:
        return tuple(key for key, _ in self.registrations())

    def models(self) -> tuple[EventModel, ...]:
        return tuple(model for _, model in self.registrations())

    def lookup(self, event_type: str, schema_version: int) -> EventModel:
        self._assert_live_metadata()
        if type(event_type) is not str or type(schema_version) is not int:
            raise EventRegistryError("event registry key has invalid types")
        try:
            return self._by_key[(event_type, schema_version)]
        except KeyError:
            raise EventRegistryError("unknown event type or schema version") from None

    def latest_version(self, event_type: str) -> int:
        self._assert_live_metadata()
        if type(event_type) is not str:
            raise EventRegistryError("event type has invalid type")
        versions = [version for name, version in self._by_key if name == event_type]
        if not versions:
            raise EventRegistryError("unknown event type")
        return max(versions)

    def dump_registered(self, payload: object) -> dict[str, JSONValue]:
        self._assert_live_metadata()
        key = self._by_model.get(type(payload))
        if key is None:
            raise EventRegistryError(
                "writer requires an exact registered payload model"
            )
        model = self._by_key[key]
        if type(payload) is not model:
            raise EventRegistryError(
                "writer requires an exact registered payload model"
            )
        dumped = payload.model_dump(mode="json")
        if type(dumped) is not dict:
            raise EventRegistryError(
                "registered payload did not serialize as an object"
            )
        canonical_json_bytes(dumped)
        return dumped

    def validate_stored(
        self,
        event_type: str,
        schema_version: int,
        stored_payload: dict[str, JSONValue],
    ) -> EventContract:
        model = self.lookup(event_type, schema_version)
        json_issue: StoredValidationIssue | None = None
        try:
            if type(stored_payload) is not dict:
                raise TypeError("stored payload is not an exact dictionary")
            canonical_json_bytes(stored_payload)
        except (TypeError, ValueError):
            json_issue = StoredValidationIssue(("$",), "invalid_json_value")
        if json_issue is not None:
            raise StoredEventValidationError(event_type, schema_version, (json_issue,))

        validation_issues: tuple[StoredValidationIssue, ...] | None = None
        try:
            return model.model_validate(stored_payload)
        except ValidationError as error:
            validation_issues = _safe_issues(error.errors(include_url=False))
        if validation_issues is not None:
            raise StoredEventValidationError(
                event_type, schema_version, validation_issues
            )
        raise AssertionError("unreachable stored event validation state")


PRODUCTION_EVENT_REGISTRY = EventRegistry(
    (
        CreditCardTransactionRecorded,
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
)
