from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionGenerationRecord,
)


PROJECTION_NAME = "monthly_category_summary"
PROJECTOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class LockedProjectionState:
    checkpoint: ProjectionCheckpointRecord | None
    generation: ProjectionGenerationRecord
    is_new: bool


def lock_projection_state(
    session: Session,
    *,
    book_id: UUID,
    target_book_position: int,
) -> LockedProjectionState:
    checkpoint = session.execute(
        select(ProjectionCheckpointRecord)
        .where(
            ProjectionCheckpointRecord.projection_name == PROJECTION_NAME,
            ProjectionCheckpointRecord.projector_version == PROJECTOR_VERSION,
            ProjectionCheckpointRecord.book_id == book_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if checkpoint is not None:
        generation = session.execute(
            select(ProjectionGenerationRecord)
            .where(
                ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
                ProjectionGenerationRecord.projector_version == PROJECTOR_VERSION,
                ProjectionGenerationRecord.book_id == book_id,
                ProjectionGenerationRecord.generation == checkpoint.active_generation,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        return LockedProjectionState(checkpoint, generation, False)

    existing = session.scalar(
        select(func.max(ProjectionGenerationRecord.generation)).where(
            ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
            ProjectionGenerationRecord.projector_version == PROJECTOR_VERSION,
            ProjectionGenerationRecord.book_id == book_id,
        )
    )
    generation_number = 1 if existing is None else int(existing) + 1
    session.execute(
        ProjectionGenerationRecord.__table__.insert().values(
            projection_name=PROJECTION_NAME,
            projector_version=PROJECTOR_VERSION,
            book_id=book_id,
            generation=generation_number,
            state="building",
            rebuild_from_position=1,
            last_book_position=0,
            target_book_position=target_book_position,
        )
    )
    generation = session.get(
        ProjectionGenerationRecord,
        (PROJECTION_NAME, PROJECTOR_VERSION, book_id, generation_number),
    )
    if generation is None:
        raise RuntimeError("projection generation could not be initialized")
    return LockedProjectionState(None, generation, True)


def activate_or_advance(
    session: Session,
    state: LockedProjectionState,
    *,
    last_book_position: int,
) -> ProjectionCheckpointRecord:
    generation = state.generation
    generation.target_book_position = max(
        generation.target_book_position,
        last_book_position,
    )
    generation.last_book_position = last_book_position
    if state.is_new:
        generation.state = "catching_up"
        session.flush([generation])
        generation.state = "active"
        session.flush([generation])
        checkpoint = ProjectionCheckpointRecord(
            projection_name=PROJECTION_NAME,
            projector_version=PROJECTOR_VERSION,
            book_id=generation.book_id,
            last_book_position=last_book_position,
            active_generation=generation.generation,
            lease_owner=None,
            lease_expires_at=None,
        )
        session.add(checkpoint)
        session.flush([checkpoint])
        return checkpoint
    checkpoint = state.checkpoint
    if checkpoint is None:
        raise AssertionError("existing projection state lost its checkpoint")
    checkpoint.last_book_position = last_book_position
    session.flush([generation, checkpoint])
    return checkpoint


__all__ = [
    "LockedProjectionState",
    "PROJECTION_NAME",
    "PROJECTOR_VERSION",
    "activate_or_advance",
    "lock_projection_state",
]
