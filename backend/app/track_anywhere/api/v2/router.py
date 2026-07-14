from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter
from sqlalchemy import Engine

from ..dependencies import SessionDependency
from .auth import create_auth_router
from .system import create_system_router


class SystemRouterFactory(Protocol):
    def __call__(
        self,
        *,
        engine: Engine | None,
        expected_runtime_role: str,
    ) -> APIRouter: ...


class AuthRouterFactory(Protocol):
    def __call__(
        self,
        get_session: SessionDependency,
        *,
        cookie_secure: bool = False,
    ) -> APIRouter: ...


def create_v2_router(
    *,
    engine: Engine | None,
    expected_runtime_role: str,
    get_session: SessionDependency,
    cookie_secure: bool = False,
    system_router_factory: SystemRouterFactory | None = None,
    auth_router_factory: AuthRouterFactory | None = None,
) -> APIRouter:
    system_factory = system_router_factory or create_system_router
    auth_factory = auth_router_factory or create_auth_router

    router = APIRouter()
    versioned_router = APIRouter(prefix="/api/v2")
    versioned_router.include_router(
        system_factory(
            engine=engine,
            expected_runtime_role=expected_runtime_role,
        )
    )
    router.include_router(versioned_router)
    router.include_router(
        auth_factory(
            get_session,
            cookie_secure=cookie_secure,
        )
    )
    if any(not route.path.startswith("/api/v2") for route in router.routes):
        raise RuntimeError("V2 composition attempted to mount an unversioned route")
    return router


__all__ = [
    "AuthRouterFactory",
    "SystemRouterFactory",
    "create_v2_router",
]
