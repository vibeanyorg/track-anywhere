from __future__ import annotations

from fastapi import APIRouter

from ...dependencies import SessionDependency
from .authorization import (
    BookReadAuthorizer,
    authorize_book_read,
    create_authorized_session_dependency,
)
from .catalog import create_catalog_query_router
from .journal import create_journal_query_router
from .reporting import create_reporting_query_router


def create_query_router(
    get_session: SessionDependency,
    *,
    authorize_book_read: BookReadAuthorizer = authorize_book_read,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["queries"])
    authorized_session = create_authorized_session_dependency(
        get_session,
        authorize_book_read,
    )
    router.include_router(create_catalog_query_router(get_session, authorized_session))
    router.include_router(create_journal_query_router(authorized_session))
    router.include_router(create_reporting_query_router(authorized_session))
    return router


__all__ = ["create_query_router"]
