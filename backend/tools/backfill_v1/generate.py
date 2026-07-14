from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Mapping
from uuid import UUID

from .namespaces import deterministic_uuid


EVENT_KIND_ORDINAL: Mapping[str, int] = {
    "journal.posted": 10,
    "reporting.assigned": 20,
    "journal.reversed": 30,
    "investment.recorded": 40,
    "valuation.recorded": 50,
}


@dataclass(frozen=True, slots=True)
class GeneratedEvent:
    event_id: UUID
    transaction_id: UUID
    effective_at: datetime
    event_kind: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if type(self.event_id) is not UUID or type(self.transaction_id) is not UUID:
            raise TypeError("generated event identities must be UUIDs")
        if self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None:
            raise ValueError("generated effective time must be timezone-aware")
        if self.event_kind not in EVENT_KIND_ORDINAL:
            raise ValueError(f"unknown generated event kind: {self.event_kind}")
        if type(self.payload) is not dict:
            raise TypeError("generated event payload must be a dictionary")


def sort_generated_events(
    events: Iterable[GeneratedEvent],
) -> tuple[GeneratedEvent, ...]:
    materialized = tuple(events)
    if any(type(event) is not GeneratedEvent for event in materialized):
        raise TypeError("generated events must contain exact GeneratedEvent values")
    return tuple(
        sorted(
            materialized,
            key=lambda event: (
                event.effective_at.astimezone(UTC),
                event.transaction_id.bytes,
                EVENT_KIND_ORDINAL[event.event_kind],
                event.event_id.bytes,
            ),
        )
    )


def generate_transaction_event(
    *,
    snapshot_id: str,
    source_book_id: str,
    source_transaction_id: str,
    effective_at: datetime,
    event_kind: str,
    payload: Mapping[str, object],
) -> GeneratedEvent:
    transaction_id = deterministic_uuid(
        "transaction",
        snapshot_id,
        source_book_id,
        source_transaction_id,
    )
    event_id = deterministic_uuid(
        "event",
        snapshot_id,
        source_book_id,
        source_transaction_id,
        event_kind,
    )
    return GeneratedEvent(
        event_id=event_id,
        transaction_id=transaction_id,
        effective_at=effective_at,
        event_kind=event_kind,
        payload={str(key): value for key, value in sorted(payload.items())},
    )


__all__ = [
    "EVENT_KIND_ORDINAL",
    "GeneratedEvent",
    "generate_transaction_event",
    "sort_generated_events",
]
