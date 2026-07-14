from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias
from uuid import UUID

from ..domain.privacy import EventContract


StreamKey: TypeAlias = tuple[str, UUID]


@dataclass(frozen=True, slots=True)
class PendingEvent:
    """A fully identified domain event waiting for a Book position."""

    event_id: UUID
    stream_type: str
    stream_id: UUID
    payload: EventContract
    command_id: UUID
    actor_subject_id: str
    correlation_id: UUID
    causation_event_id: UUID | None
    effective_at: datetime

    @property
    def stream_key(self) -> StreamKey:
        return self.stream_type, self.stream_id


@dataclass(frozen=True, slots=True)
class AppendBatchResult:
    positions: range
    terminal_hash: bytes
    event_ids: tuple[UUID, ...]


__all__ = ["AppendBatchResult", "PendingEvent", "StreamKey"]
