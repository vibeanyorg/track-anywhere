from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...domain.privacy import EventContract
from ...serialization.event_registry import PRODUCTION_EVENT_REGISTRY
from ..db.models.event_store import LedgerEventRecord
from ..db.models.projections import (
    SynchronousProjectionAppliedEventRecord,
    SynchronousProjectionEventTypeRecord,
)
from .synchronous_appliers.contracts import (
    SynchronousProjectionError,
    TypedEventApplier,
)
from .synchronous_appliers.registry import SYNCHRONOUS_APPLIERS


@dataclass(frozen=True, slots=True)
class ProjectionApplyResult:
    event_id: UUID
    required: bool
    applied: bool
    projection_version: int | None


class SynchronousProjector:
    """Coordinate required projections in the event append transaction."""

    def apply_stored(
        self,
        session: Session,
        stored: LedgerEventRecord,
    ) -> ProjectionApplyResult:
        if type(stored) is not LedgerEventRecord:
            raise SynchronousProjectionError("stored event has an invalid runtime type")
        required = session.get(
            SynchronousProjectionEventTypeRecord,
            (stored.event_type, stored.event_schema_version),
        )
        if required is None:
            return ProjectionApplyResult(
                event_id=stored.event_id,
                required=False,
                applied=False,
                projection_version=None,
            )

        payload = PRODUCTION_EVENT_REGISTRY.validate_stored(
            stored.event_type,
            stored.event_schema_version,
            stored.payload,
        )
        applier = self._applier_for(payload)
        inserted = session.execute(
            insert(SynchronousProjectionAppliedEventRecord)
            .values(
                book_id=stored.book_id,
                event_id=stored.event_id,
                projection_version=required.projection_version,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    SynchronousProjectionAppliedEventRecord.book_id,
                    SynchronousProjectionAppliedEventRecord.event_id,
                )
            )
            .returning(SynchronousProjectionAppliedEventRecord.event_id)
        ).scalar_one_or_none()
        if inserted is None:
            return ProjectionApplyResult(
                event_id=stored.event_id,
                required=True,
                applied=False,
                projection_version=required.projection_version,
            )

        applier.apply(session, stored, payload)
        session.flush()
        return ProjectionApplyResult(
            event_id=stored.event_id,
            required=True,
            applied=True,
            projection_version=required.projection_version,
        )

    @staticmethod
    def _applier_for(payload: EventContract) -> TypedEventApplier:
        applier = SYNCHRONOUS_APPLIERS.get(type(payload))
        if applier is None:
            raise SynchronousProjectionError(
                "registered synchronous event has no projection applier"
            )
        return applier


__all__ = [
    "ProjectionApplyResult",
    "SYNCHRONOUS_APPLIERS",
    "SynchronousProjectionError",
    "SynchronousProjector",
]
