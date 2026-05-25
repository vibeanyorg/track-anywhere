from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .security import Actor, redact, utcnow


@dataclass
class AuditEvent:
    event_id: str
    operation: str
    actor_id: str
    actor_type: str
    entity_ref: str | None
    details: dict[str, Any]
    created_at: str = field(default_factory=lambda: utcnow().isoformat())


class AuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._persisted_count = 0

    def record(
        self,
        *,
        operation: str,
        actor: Actor,
        entity_ref: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"audit_{uuid4().hex}",
            operation=operation,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            entity_ref=entity_ref,
            details=redact(details or {}),
        )
        self.events.append(event)
        return event

    def pending_events(self) -> list[AuditEvent]:
        return self.events[self._persisted_count :]

    def mark_persisted(self) -> None:
        self._persisted_count = len(self.events)

    def for_entity(self, entity_ref: str) -> list[AuditEvent]:
        return [event for event in self.events if event.entity_ref == entity_ref]
