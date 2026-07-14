from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db.models.async_projection import (
    ProjectionCheckpointRecord,
    ProjectionGenerationRecord,
)
from ..db.models.event_store import BookEventHeadRecord
from .checkpoints import PROJECTION_NAME, PROJECTOR_VERSION
from .monthly_summary import replace_generation


@dataclass(frozen=True, slots=True)
class ProjectionRebuildResult:
    previous_generation: int
    active_generation: int
    last_book_position: int


class ShadowProjectionRebuilder:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        after_shadow_built: Callable[[], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._after_shadow_built = after_shadow_built

    def rebuild_book(self, book_id: UUID) -> ProjectionRebuildResult:
        shadow_generation = self._build_or_resume_shadow(book_id)
        if self._after_shadow_built is not None:
            self._after_shadow_built()
        return self._catch_up_and_swap(book_id, shadow_generation)

    def _build_or_resume_shadow(self, book_id: UUID) -> int:
        with self._session_factory() as session, session.begin():
            checkpoint = session.get(
                ProjectionCheckpointRecord,
                (PROJECTION_NAME, PROJECTOR_VERSION, book_id),
            )
            if checkpoint is None:
                raise RuntimeError("projection must be active before shadow rebuild")
            shadow = session.execute(
                select(ProjectionGenerationRecord)
                .where(
                    ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
                    ProjectionGenerationRecord.projector_version == PROJECTOR_VERSION,
                    ProjectionGenerationRecord.book_id == book_id,
                    ProjectionGenerationRecord.state.in_(("building", "catching_up")),
                )
                .order_by(ProjectionGenerationRecord.generation.desc())
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()
            if shadow is not None and shadow.state == "catching_up":
                return shadow.generation
            head = session.get(BookEventHeadRecord, book_id)
            if head is None:
                raise LookupError("Book event head not found")
            if shadow is None:
                maximum = session.scalar(
                    select(func.max(ProjectionGenerationRecord.generation)).where(
                        ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
                        ProjectionGenerationRecord.projector_version
                        == PROJECTOR_VERSION,
                        ProjectionGenerationRecord.book_id == book_id,
                    )
                )
                generation = int(maximum or 0) + 1
                session.execute(
                    ProjectionGenerationRecord.__table__.insert().values(
                        projection_name=PROJECTION_NAME,
                        projector_version=PROJECTOR_VERSION,
                        book_id=book_id,
                        generation=generation,
                        state="building",
                        rebuild_from_position=1,
                        last_book_position=0,
                        target_book_position=head.last_position,
                    )
                )
                shadow = session.get(
                    ProjectionGenerationRecord,
                    (PROJECTION_NAME, PROJECTOR_VERSION, book_id, generation),
                )
                if shadow is None:
                    raise RuntimeError("shadow generation could not be created")
            replace_generation(
                session,
                book_id=book_id,
                generation=shadow.generation,
                through_book_position=head.last_position,
            )
            shadow.target_book_position = head.last_position
            shadow.last_book_position = head.last_position
            shadow.state = "catching_up"
            session.flush([shadow])
            return shadow.generation

    def _catch_up_and_swap(
        self,
        book_id: UUID,
        shadow_generation: int,
    ) -> ProjectionRebuildResult:
        with self._session_factory() as session, session.begin():
            session.execute(
                text("select pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _advisory_key(book_id)},
            )
            checkpoint = session.execute(
                select(ProjectionCheckpointRecord)
                .where(
                    ProjectionCheckpointRecord.projection_name == PROJECTION_NAME,
                    ProjectionCheckpointRecord.projector_version == PROJECTOR_VERSION,
                    ProjectionCheckpointRecord.book_id == book_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            previous_generation = checkpoint.active_generation
            active = session.execute(
                select(ProjectionGenerationRecord)
                .where(
                    ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
                    ProjectionGenerationRecord.projector_version == PROJECTOR_VERSION,
                    ProjectionGenerationRecord.book_id == book_id,
                    ProjectionGenerationRecord.generation == previous_generation,
                )
                .with_for_update()
            ).scalar_one()
            shadow = session.execute(
                select(ProjectionGenerationRecord)
                .where(
                    ProjectionGenerationRecord.projection_name == PROJECTION_NAME,
                    ProjectionGenerationRecord.projector_version == PROJECTOR_VERSION,
                    ProjectionGenerationRecord.book_id == book_id,
                    ProjectionGenerationRecord.generation == shadow_generation,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            if shadow.state != "catching_up":
                raise RuntimeError("shadow generation is not resumable")
            head = session.get(BookEventHeadRecord, book_id)
            if head is None:
                raise LookupError("Book event head not found")
            replace_generation(
                session,
                book_id=book_id,
                generation=shadow_generation,
                through_book_position=head.last_position,
            )
            shadow.target_book_position = head.last_position
            shadow.last_book_position = head.last_position
            session.flush([shadow])
            active.state = "retired"
            session.flush([active])
            shadow.state = "active"
            session.flush([shadow])
            checkpoint.active_generation = shadow_generation
            checkpoint.last_book_position = head.last_position
            session.flush([checkpoint])
            return ProjectionRebuildResult(
                previous_generation=previous_generation,
                active_generation=shadow_generation,
                last_book_position=head.last_position,
            )


def _advisory_key(book_id: UUID) -> int:
    unsigned = int.from_bytes(book_id.bytes[:8], "big", signed=False)
    return unsigned if unsigned < 2**63 else unsigned - 2**64


__all__ = [
    "ProjectionRebuildResult",
    "ShadowProjectionRebuilder",
]
