from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ...infrastructure.db.models.catalog import CategoryRecord, CategoryVersionRecord
from ...infrastructure.db.models.event_store import BookEventHeadRecord
from ..idempotency import CommandActor
from ..unit_of_work import UnitOfWork
from ._authorization import require_catalog_write


@dataclass(frozen=True, slots=True)
class CreateCategory:
    book_id: UUID
    category_id: UUID
    category_version_id: UUID
    name: str
    parent_category_id: UUID | None
    change_reason_code: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not UUID
            for value in (self.book_id, self.category_id, self.category_version_id)
        ):
            raise ValueError("category identifiers must be UUIDs")
        if (
            self.parent_category_id is not None
            and type(self.parent_category_id) is not UUID
        ):
            raise ValueError("parent_category_id must be a UUID or null")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("name must be nonblank")
        if (
            type(self.change_reason_code) is not str
            or not self.change_reason_code.strip()
        ):
            raise ValueError("change_reason_code must be nonblank")


def create_category(
    command: CreateCategory,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
) -> dict[str, object]:
    with uow_factory() as uow:
        require_catalog_write(uow.session, actor, command.book_id)
        head = uow.session.get(BookEventHeadRecord, command.book_id)
        if head is None:
            raise ValueError("Book event head is unavailable")
        category = CategoryRecord(
            book_id=command.book_id,
            category_id=command.category_id,
            parent_category_id=command.parent_category_id,
            current_name=command.name.strip(),
            current_version_id=None,
            status="active",
        )
        uow.session.add(category)
        uow.session.flush()
        uow.session.add(
            CategoryVersionRecord(
                book_id=command.book_id,
                category_id=command.category_id,
                category_version_id=command.category_version_id,
                parent_category_id=command.parent_category_id,
                name=command.name.strip(),
                status="active",
                change_reason_code=command.change_reason_code.strip(),
            )
        )
        uow.session.flush()
        category.current_version_id = command.category_version_id
        uow.session.flush()
        return {
            "category_id": str(command.category_id),
            "category_version_id": str(command.category_version_id),
            "as_of_book_position": head.last_position,
        }


__all__ = ["CreateCategory", "create_category"]
