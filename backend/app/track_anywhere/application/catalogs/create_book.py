from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from ...infrastructure.db.models.auth import BookMemberRecord, UserRecord
from ...infrastructure.db.models.catalog import AssetRecord, BookRecord
from ...infrastructure.db.models.event_store import BookEventHeadRecord
from ..idempotency import CommandActor
from ..unit_of_work import UnitOfWork


class BookCreationForbidden(PermissionError):
    pass


class BaseAssetUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreateBook:
    book_id: UUID
    current_name: str
    base_asset_code: str | None

    def __post_init__(self) -> None:
        if type(self.book_id) is not UUID:
            raise ValueError("book_id must be a UUID")
        if type(self.current_name) is not str or not self.current_name.strip():
            raise ValueError("current_name must be nonblank")
        if self.base_asset_code is not None and (
            type(self.base_asset_code) is not str
            or not self.base_asset_code
            or self.base_asset_code != self.base_asset_code.upper()
        ):
            raise ValueError("base_asset_code is invalid")


def _new_book_head(book_id: UUID) -> BookEventHeadRecord:
    return BookEventHeadRecord(book_id=book_id, last_position=0, last_hash=bytes(32))


def create_book(
    command: CreateBook,
    *,
    actor: CommandActor,
    uow_factory: Callable[[], UnitOfWork],
) -> dict[str, object]:
    with uow_factory() as uow:
        user = uow.session.scalar(
            select(UserRecord)
            .where(UserRecord.user_id == actor.subject_id)
            .with_for_update()
        )
        if user is None or user.status != "active":
            raise BookCreationForbidden("Book creation is not authorized")
        if command.base_asset_code is not None:
            asset = uow.session.get(AssetRecord, command.base_asset_code)
            if asset is None or asset.status != "active":
                raise BaseAssetUnavailable("base asset is unavailable")
        book = BookRecord(
            book_id=command.book_id,
            current_name=command.current_name.strip(),
            base_asset_code=command.base_asset_code,
            write_state="active",
        )
        uow.session.add(book)
        # The V2 mappings deliberately carry no ORM relationships. Flush the
        # parent explicitly so PostgreSQL, rather than mapper heuristics, owns
        # the FK ordering while the outer UoW still keeps the operation atomic.
        uow.session.flush()
        uow.session.add(
            BookMemberRecord(
                book_id=command.book_id,
                user_id=actor.subject_id,
                role="owner",
                status="active",
                scopes=["book:read", "book:write", "ledger:read", "ledger:write"],
                revoked_at=None,
            )
        )
        uow.session.add(_new_book_head(command.book_id))
        uow.session.flush()
        return {"book_id": str(command.book_id), "as_of_book_position": 0}


__all__ = [
    "BaseAssetUnavailable",
    "BookCreationForbidden",
    "CreateBook",
    "create_book",
]
