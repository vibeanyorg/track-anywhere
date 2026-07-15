from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ....infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    AuthRepository,
)
from ...dependencies import SessionDependency
from ..schemas import authenticate_request_actor


BOOK_READ_SCOPE = "ledger:read"
BookReadAuthorizer = Callable[[Session, Request, UUID], None]
AuthorizedSessionDependency = Callable[..., Session]


def authorize_book_read(
    session: Session,
    request: Request,
    book_id: UUID,
) -> None:
    identity = authenticate_request_actor(session, request)
    if (
        identity.credential_book_id is not None
        and identity.credential_book_id != book_id
    ):
        raise book_access_denied()
    if BOOK_READ_SCOPE not in identity.scopes:
        raise book_access_denied()
    try:
        membership = AuthRepository(session).get_membership(
            book_id,
            identity.command_actor.subject_id,
        )
    except AuthRecordNotFound as error:
        raise book_access_denied() from error
    if (
        membership.status != "active"
        or membership.revoked_at is not None
        or BOOK_READ_SCOPE not in membership.scopes
    ):
        raise book_access_denied()


def create_authorized_session_dependency(
    get_session: SessionDependency,
    authorize: BookReadAuthorizer,
) -> AuthorizedSessionDependency:
    def authorized_session(
        request: Request,
        book_id: UUID,
        session: Session = Depends(get_session),
    ) -> Session:
        authorize(session, request, book_id)
        return session

    return authorized_session


def book_access_denied() -> HTTPException:
    return HTTPException(status_code=403, detail="Book read access is denied")


__all__ = [
    "BOOK_READ_SCOPE",
    "AuthorizedSessionDependency",
    "BookReadAuthorizer",
    "authorize_book_read",
    "book_access_denied",
    "create_authorized_session_dependency",
]
