from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar, cast

from sqlalchemy.orm import Session

from ....domain.privacy import EventContract
from ...db.models.event_store import LedgerEventRecord


class SynchronousProjectionError(RuntimeError):
    pass


PayloadT = TypeVar("PayloadT", bound=EventContract)


@dataclass(frozen=True, slots=True)
class TypedEventApplier:
    payload_type: type[EventContract]
    handler: Callable[..., None]
    _invoke: Callable[[Session, LedgerEventRecord, EventContract], None] = field(
        repr=False
    )

    def apply(
        self,
        session: Session,
        stored: LedgerEventRecord,
        payload: EventContract,
    ) -> None:
        self._invoke(session, stored, payload)


def typed_event_applier(
    payload_type: type[PayloadT],
    handler: Callable[[Session, LedgerEventRecord, PayloadT], None],
) -> TypedEventApplier:
    def invoke(
        session: Session,
        stored: LedgerEventRecord,
        payload: EventContract,
    ) -> None:
        if type(payload) is not payload_type:
            raise SynchronousProjectionError(
                "synchronous projection applier received an invalid payload type"
            )
        handler(session, stored, cast(PayloadT, payload))

    return TypedEventApplier(
        payload_type=payload_type,
        handler=handler,
        _invoke=invoke,
    )


__all__ = [
    "SynchronousProjectionError",
    "TypedEventApplier",
    "typed_event_applier",
]
