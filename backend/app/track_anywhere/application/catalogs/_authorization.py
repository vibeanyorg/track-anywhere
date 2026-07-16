from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ...infrastructure.db.repositories import RowLock
from ...infrastructure.db.repositories.auth import AuthRecordNotFound, AuthRepository
from ..idempotency import AuthorizationScope, CommandActor


class CatalogWriteForbidden(PermissionError):
    pass


def authorize_catalog_write(
    session: Session,
    actor: CommandActor,
    book_id: UUID,
    *,
    lock_membership: bool,
) -> AuthorizationScope:
    try:
        member = AuthRepository(session).get_membership(
            book_id,
            actor.subject_id,
            lock=RowLock.SHARE if lock_membership else RowLock.NONE,
        )
    except AuthRecordNotFound:
        raise CatalogWriteForbidden("catalog write is not authorized") from None
    if (
        member.status != "active"
        or member.revoked_at is not None
        or "book:write" not in member.scopes
    ):
        raise CatalogWriteForbidden("catalog write is not authorized")
    return AuthorizationScope(
        book_id=book_id,
        actor_subject_id=actor.subject_id,
        role=member.role,
        scopes=member.scopes,
    )


def require_catalog_write(session: Session, actor: CommandActor, book_id: UUID) -> None:
    authorize_catalog_write(
        session,
        actor,
        book_id,
        lock_membership=True,
    )


__all__ = [
    "CatalogWriteForbidden",
    "authorize_catalog_write",
    "require_catalog_write",
]
