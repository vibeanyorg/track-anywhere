from __future__ import annotations

import os
from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ..auth.resources import configured_public_base_url
from ..infrastructure.crypto import ProtectedContentCipher
from .dependencies import (
    DATABASE_URL_ENV,
    RuntimeDependencies,
    SessionDependency,
    build_engine_dependencies,
    build_runtime_dependencies,
)
from .errors import install_error_handlers
from .v2.router import (
    AuthRouterFactory,
    SystemRouterFactory,
    create_v2_router,
)


def create_app(
    *,
    dependencies: RuntimeDependencies | None = None,
    engine: Engine | None = None,
    expected_runtime_role: str | None = None,
    get_session: SessionDependency | None = None,
    system_router_factory: SystemRouterFactory | None = None,
    auth_router_factory: AuthRouterFactory | None = None,
    cookie_secure: bool | None = None,
    public_base_url: str | None = None,
    protected_content_cipher: ProtectedContentCipher | None = None,
) -> FastAPI:
    if dependencies is not None and (
        engine is not None
        or get_session is not None
        or protected_content_cipher is not None
    ):
        raise ValueError(
            "dependencies cannot be combined with engine or get_session overrides"
        )
    runtime = dependencies
    if runtime is None and engine is not None:
        runtime = build_engine_dependencies(
            engine,
            expected_runtime_role=expected_runtime_role,
            protected_content_cipher=protected_content_cipher,
        )
    if runtime is None and engine is None and get_session is None:
        database_url = os.environ.get(DATABASE_URL_ENV)
        if database_url:
            # SQLAlchemy engine construction is lazy: this wires the runtime
            # composition root without opening a socket during module import.
            runtime = build_runtime_dependencies(database_url)
    composed_engine = None if runtime is None else runtime.engine
    composed_protected_content_cipher = (
        protected_content_cipher
        if runtime is None
        else runtime.protected_content_cipher
    )
    composed_role = (
        expected_runtime_role
        if expected_runtime_role is not None
        else "" if runtime is None else runtime.expected_runtime_role
    )
    session_dependency = (
        get_session
        if get_session is not None
        else _database_unavailable_session
        if runtime is None
        else runtime.get_session
    )
    secure_cookie = _cookie_secure_from_env() if cookie_secure is None else cookie_secure
    public_base = public_base_url or configured_public_base_url()
    composed_auth_factory = auth_router_factory
    if composed_auth_factory is None:
        from .v2.auth import create_auth_router

        def composed_auth_factory(
            dependency: SessionDependency,
            *,
            cookie_secure: bool = False,
        ):
            return create_auth_router(
                dependency,
                cookie_secure=cookie_secure,
                public_base_url=public_base,
            )

    application = FastAPI(
        title="Track Anywhere API",
        version="2.0.0",
        openapi_url="/api/v2/openapi.json",
        docs_url="/api/v2/docs",
        swagger_ui_oauth2_redirect_url="/api/v2/docs/oauth2-redirect",
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins_from_env(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Idempotency-Key",
            "X-CSRF-Token",
        ],
    )
    application.include_router(
        create_v2_router(
            engine=composed_engine,
            expected_runtime_role=composed_role,
            get_session=session_dependency,
            cookie_secure=secure_cookie,
            system_router_factory=system_router_factory,
            auth_router_factory=composed_auth_factory,
            protected_content_cipher=composed_protected_content_cipher,
        )
    )
    install_error_handlers(application)
    application.state.runtime_dependencies = runtime
    application.state.public_base_url = public_base
    return application


def _database_unavailable_session() -> Iterator[Session]:
    raise RuntimeError("runtime database is not configured")
    yield


def _allowed_origins_from_env() -> list[str]:
    raw = os.environ.get(
        "TRACK_ANYWHERE_ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    )
    origins = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    return origins or ["http://localhost:8000"]


def _cookie_secure_from_env() -> bool:
    raw = os.environ.get("TRACK_ANYWHERE_AUTH_COOKIE_SECURE")
    if raw is None:
        return os.environ.get("TRACK_ANYWHERE_MODE", "local") != "local"
    return raw.lower() in {"1", "true", "yes", "on"}


app = create_app()


__all__ = ["app", "create_app"]
