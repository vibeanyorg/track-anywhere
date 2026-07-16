from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, FastAPI
from sqlalchemy import Engine

from ...infrastructure.crypto import ProtectedContentCipher
from ..dependencies import SessionDependency, build_engine_dependencies
from .auth import create_auth_router
from .catalogs import create_catalog_router
from .credit_cards import create_credit_card_router
from .investments import create_investment_router
from .journal import create_journal_router
from .queries import create_query_router
from .reporting import create_reporting_router
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
    protected_content_cipher: ProtectedContentCipher | None = None,
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
    if engine is not None:
        runtime = build_engine_dependencies(
            engine,
            expected_runtime_role=expected_runtime_role,
        )
        versioned_router.include_router(
            create_catalog_router(
                get_session=get_session,
                uow_factory=runtime.uow_factory,
                ledger_committer=runtime.ledger_committer,
            )
        )
        versioned_router.include_router(
            create_journal_router(
                get_session=get_session,
                uow_factory=runtime.uow_factory,
                ledger_committer=runtime.ledger_committer,
            )
        )
        versioned_router.include_router(
            create_credit_card_router(
                get_session=get_session,
                uow_factory=runtime.uow_factory,
                ledger_committer=runtime.ledger_committer,
            )
        )
        versioned_router.include_router(
            create_reporting_router(
                get_session=get_session,
                uow_factory=runtime.uow_factory,
                ledger_committer=runtime.ledger_committer,
            )
        )
        versioned_router.include_router(
            create_investment_router(
                get_session=get_session,
                uow_factory=runtime.uow_factory,
                ledger_committer=runtime.ledger_committer,
            )
        )
    router.include_router(versioned_router)
    if engine is not None:
        router.include_router(
            create_query_router(
                get_session,
                protected_content_cipher=protected_content_cipher,
            )
        )
    router.include_router(
        auth_factory(
            get_session,
            cookie_secure=cookie_secure,
        )
    )
    route_probe = FastAPI()
    route_probe.include_router(router)
    if any(not path.startswith("/api/v2") for path in route_probe.openapi()["paths"]):
        raise RuntimeError("V2 composition attempted to mount an unversioned route")
    return router


__all__ = [
    "AuthRouterFactory",
    "SystemRouterFactory",
    "create_v2_router",
]
