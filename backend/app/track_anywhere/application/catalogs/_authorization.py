from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ...infrastructure.db.repositories import RowLock
from ...infrastructure.db.repositories.auth import AuthRecordNotFound, AuthRepository
from ..idempotency import CommandActor


class CatalogWriteForbidden(PermissionError):
    pass


def require_catalog_write(session: Session, actor: CommandActor, book_id: UUID) -> None:
    try:
        member = AuthRepository(session).get_membership(
            book_id,
            actor.subject_id,
            lock=RowLock.SHARE,
        )
    except AuthRecordNotFound:
        raise CatalogWriteForbidden("catalog write is not authorized") from None
    if (
        member.status != "active"
        or member.revoked_at is not None
        or "book:write" not in member.scopes
    ):
        raise CatalogWriteForbidden("catalog write is not authorized")


__all__ = ["CatalogWriteForbidden", "require_catalog_write"]
