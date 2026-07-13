from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..domain.privacy import EventContract
from .canonical_json import JSONValue, canonical_json_bytes
from .event_registry import (
    PRODUCTION_EVENT_REGISTRY,
    EventRegistry,
    EventRegistryError,
    StoredEventValidationError,
)


UpcastFunction = Callable[[dict[str, JSONValue]], dict[str, JSONValue]]


class UpcastError(ValueError):
    """A fail-closed upcaster configuration or execution error."""


@dataclass(frozen=True, slots=True)
class UpcastStep:
    event_type: str
    from_version: int
    to_version: int
    transform: UpcastFunction


class UpcasterRegistry:
    def __init__(
        self,
        event_registry: EventRegistry,
        steps: Sequence[UpcastStep],
    ) -> None:
        if type(event_registry) is not EventRegistry:
            raise UpcastError("upcasters require an EventRegistry")
        by_source: dict[tuple[str, int], UpcastStep] = {}
        for step in steps:
            if type(step) is not UpcastStep:
                raise UpcastError("upcast steps must be exact UpcastStep values")
            if (
                type(step.event_type) is not str
                or type(step.from_version) is not int
                or type(step.to_version) is not int
            ):
                raise UpcastError("upcast step metadata has invalid types")
            if step.to_version != step.from_version + 1:
                raise UpcastError("upcast steps must target adjacent schema versions")
            if not callable(step.transform):
                raise UpcastError("upcast transform must be callable")
            source = (step.event_type, step.from_version)
            if source in by_source:
                raise UpcastError("duplicate upcast source step")
            unknown_schema = False
            try:
                event_registry.lookup(step.event_type, step.from_version)
                event_registry.lookup(step.event_type, step.to_version)
            except EventRegistryError:
                unknown_schema = True
            if unknown_schema:
                raise UpcastError("unknown upcast source or target schema")
            by_source[source] = step

        event_types = sorted({event_type for event_type, _ in event_registry.keys()})
        for event_type in event_types:
            versions = sorted(
                version
                for registered_type, version in event_registry.keys()
                if registered_type == event_type
            )
            expected_versions = list(range(versions[0], versions[-1] + 1))
            if versions != expected_versions:
                raise UpcastError("missing registered schema in upcast chain")
            for version in versions[:-1]:
                if (event_type, version) not in by_source:
                    raise UpcastError("missing upcast step in registered schema chain")

        self._event_registry = event_registry
        self._by_source = by_source

    def _apply_step(
        self,
        step: UpcastStep,
        payload: dict[str, JSONValue],
    ) -> dict[str, JSONValue]:
        baseline = copy.deepcopy(payload)
        first_input = copy.deepcopy(payload)
        second_input = copy.deepcopy(payload)
        transform_failed = False
        first: object = None
        second: object = None
        try:
            first = step.transform(first_input)
            second = step.transform(second_input)
        except Exception:
            transform_failed = True
        if transform_failed:
            raise UpcastError("upcast transform raised an exception")
        if first_input != baseline or second_input != baseline:
            raise UpcastError("upcast transform must not mutate its input")
        if type(first) is not dict or type(second) is not dict:
            raise UpcastError("upcast transform must return an exact mapping object")
        invalid_json = False
        first_bytes = b""
        second_bytes = b""
        try:
            first_bytes = canonical_json_bytes(first)
            second_bytes = canonical_json_bytes(second)
        except (TypeError, ValueError):
            invalid_json = True
        if invalid_json:
            raise UpcastError("upcast transform returned an invalid JSON mapping")
        if first_bytes != second_bytes:
            raise UpcastError("upcast transform must be deterministic")
        target_invalid = False
        try:
            self._event_registry.validate_stored(
                step.event_type,
                step.to_version,
                first,
            )
        except StoredEventValidationError:
            target_invalid = True
        if target_invalid:
            raise UpcastError("upcast target validation failed")
        return copy.deepcopy(first)

    def upcast_to_latest(
        self,
        event_type: str,
        from_version: int,
        stored_payload: dict[str, JSONValue],
    ) -> EventContract:
        source_invalid = False
        latest = 0
        try:
            self._event_registry.lookup(event_type, from_version)
            latest = self._event_registry.latest_version(event_type)
            self._event_registry.validate_stored(
                event_type,
                from_version,
                stored_payload,
            )
        except (EventRegistryError, StoredEventValidationError):
            source_invalid = True
        if source_invalid:
            raise UpcastError("upcast source validation failed")

        current = copy.deepcopy(stored_payload)
        version = from_version
        while version < latest:
            step = self._by_source.get((event_type, version))
            if step is None:
                raise UpcastError("missing upcast step in registered schema chain")
            current = self._apply_step(step, current)
            version = step.to_version
        final_invalid = False
        result: EventContract | None = None
        try:
            result = self._event_registry.validate_stored(
                event_type,
                version,
                current,
            )
        except StoredEventValidationError:
            final_invalid = True
        if final_invalid or result is None:
            raise UpcastError("upcast target validation failed")
        return result


PRODUCTION_UPCASTERS = UpcasterRegistry(PRODUCTION_EVENT_REGISTRY, ())
