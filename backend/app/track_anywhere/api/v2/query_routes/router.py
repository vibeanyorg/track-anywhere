from __future__ import annotations

from fastapi import APIRouter

from ....infrastructure.crypto import ProtectedContentCipher
from ...dependencies import SessionDependency
from .authorization import (
    BookOwnerReadAuthorizer,
    BookReadAuthorizer,
    authorize_book_owner_read,
    authorize_book_read,
    create_authorized_session_dependency,
)
from .catalog import create_catalog_query_router
from .journal import create_journal_query_router
from .protected_content import create_protected_content_query_router
from .reporting import create_reporting_query_router


def create_query_router(
    get_session: SessionDependency,
    *,
    authorize_book_read: BookReadAuthorizer = authorize_book_read,
    authorize_book_owner_read: BookOwnerReadAuthorizer = authorize_book_owner_read,
    protected_content_cipher: ProtectedContentCipher | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["queries"])
    authorized_session = create_authorized_session_dependency(
        get_session,
        authorize_book_read,
    )
    owner_authorized_session = create_authorized_session_dependency(
        get_session,
        authorize_book_owner_read,
    )
    router.include_router(create_catalog_query_router(get_session, authorized_session))
    router.include_router(
        create_journal_query_router(
            authorized_session,
            authorize_book_owner_read=authorize_book_owner_read,
            protected_content_cipher=protected_content_cipher,
        )
    )
    router.include_router(
        create_protected_content_query_router(
            owner_authorized_session,
            protected_content_cipher=protected_content_cipher,
        )
    )
    router.include_router(create_reporting_query_router(authorized_session))
    return router


__all__ = ["create_query_router"]
