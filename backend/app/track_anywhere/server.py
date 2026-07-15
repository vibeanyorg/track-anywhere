from __future__ import annotations

import math
import os
import stat
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api.app import create_app as create_api_app
from .api.dependencies import RuntimeDependencies
from .auth.resources import api_resource, configured_public_base_url
from .auth.security import authorization_server_metadata, protected_resource_metadata
from .infrastructure.projections.runtime import ProjectionRuntime
from .mcp.server import McpRuntime, create_mcp_runtime

STATIC_DIRECTORY_ENV = "TRACK_ANYWHERE_STATIC_DIRECTORY"
PROJECTION_POLL_SECONDS_ENV = "TRACK_ANYWHERE_PROJECTION_POLL_SECONDS"


class StaticExportFiles(StaticFiles):
    """Serve exported route indexes without scheme-sensitive redirects."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] in {"GET", "HEAD"} and not path.endswith("/"):
            try:
                full_path, stat_result = await anyio.to_thread.run_sync(
                    self.lookup_path,
                    f"{path}/index.html",
                )
            except (OSError, ValueError):
                pass
            else:
                if stat_result is not None and stat.S_ISREG(stat_result.st_mode):
                    return self.file_response(full_path, stat_result, scope)
        return await super().get_response(path, scope)


class ProtocolApplication:
    def __init__(
        self,
        *,
        rest_application: FastAPI,
        discovery_application: FastAPI,
        mcp_runtime: McpRuntime | None,
        web_application: ASGIApp | None = None,
        projection_runtime: ProjectionRuntime | None = None,
    ) -> None:
        self.rest_application = rest_application
        self.discovery_application = discovery_application
        self.mcp_runtime = mcp_runtime
        self.web_application = web_application
        self.projection_runtime = projection_runtime
        self.state = rest_application.state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._run_lifespan(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if path.startswith("/.well-known/"):
            if (
                self.mcp_runtime is not None
                and path == self.mcp_runtime.protected_resource_metadata_path
            ):
                await self.mcp_runtime.application(scope, receive, send)
            else:
                await self.discovery_application(scope, receive, send)
            return
        if path in {"/mcp", "/mcp/"}:
            target = (
                self.mcp_runtime.application
                if self.mcp_runtime is not None
                else self.discovery_application
            )
            await target(scope, receive, send)
            return
        if self.web_application is not None and _is_web_request(scope, path):
            await self.web_application(scope, receive, _cache_headers(path, send))
            return
        await self.rest_application(scope, receive, send)

    async def _run_lifespan(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        async def run_rest_with_projection() -> None:
            if self.projection_runtime is None:
                await self.rest_application(scope, receive, send)
                return
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self.projection_runtime.run_forever)
                try:
                    await self.rest_application(scope, receive, send)
                finally:
                    tasks.cancel_scope.cancel()

        if self.mcp_runtime is None:
            await run_rest_with_projection()
            return
        async with self.mcp_runtime.server.session_manager.run():
            await run_rest_with_projection()


def create_server(
    *,
    dependencies: RuntimeDependencies | None = None,
    public_base_url: str | None = None,
    static_directory: str | Path | None = None,
    **api_options: Any,
) -> ProtocolApplication:
    base = public_base_url or configured_public_base_url()
    rest = create_api_app(
        dependencies=dependencies,
        public_base_url=base,
        **api_options,
    )
    runtime = rest.state.runtime_dependencies
    mcp_runtime = None if runtime is None else create_mcp_runtime(runtime, base)
    projection_runtime = (
        None
        if runtime is None
        else ProjectionRuntime(
            runtime.session_factory,
            poll_seconds=_projection_poll_seconds(),
        )
    )
    discovery = _create_discovery_application(base, mcp_runtime is not None)
    web_application = _create_web_application(static_directory)
    return ProtocolApplication(
        rest_application=rest,
        discovery_application=discovery,
        mcp_runtime=mcp_runtime,
        web_application=web_application,
        projection_runtime=projection_runtime,
    )


def _projection_poll_seconds() -> float:
    raw_value = os.environ.get(PROJECTION_POLL_SECONDS_ENV, "2")
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{PROJECTION_POLL_SECONDS_ENV} must be between 0.1 and 300 seconds"
        ) from None
    if not math.isfinite(value) or not 0.1 <= value <= 300:
        raise ValueError(
            f"{PROJECTION_POLL_SECONDS_ENV} must be between 0.1 and 300 seconds"
        )
    return value


def _create_web_application(
    configured_directory: str | Path | None,
) -> StaticExportFiles | None:
    raw_directory = (
        configured_directory
        if configured_directory is not None
        else os.environ.get(STATIC_DIRECTORY_ENV)
    )
    if raw_directory is None or not str(raw_directory).strip():
        return None
    directory = Path(raw_directory).expanduser().resolve()
    index = directory / "index.html"
    if not index.is_file():
        raise ValueError(
            f"configured static export must contain index.html: {directory}"
        )
    return StaticExportFiles(directory=directory, html=True, check_dir=True)


def _is_web_request(scope: Scope, path: str) -> bool:
    if scope["type"] != "http":
        return False
    if str(scope.get("method", "GET")).upper() not in {"GET", "HEAD"}:
        return False
    return not (path == "/api" or path.startswith("/api/"))


def _cache_headers(path: str, send: Send) -> Send:
    async def send_with_cache_headers(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            status = int(message.get("status", 0))
            if path.startswith("/_next/static/") and status in {200, 206, 304}:
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                headers["Cache-Control"] = "no-cache"
        await send(message)

    return send_with_cache_headers


def _create_discovery_application(
    public_base_url: str,
    mcp_enabled: bool,
) -> FastAPI:
    application = FastAPI(
        title="Track Anywhere OAuth Discovery",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/.well-known/oauth-authorization-server")
    def authorization_server() -> dict[str, object]:
        return authorization_server_metadata(public_base_url)

    @application.get("/.well-known/oauth-protected-resource")
    @application.get("/.well-known/oauth-protected-resource/api/v2")
    def api_protected_resource() -> dict[str, object]:
        return protected_resource_metadata(
            public_base_url,
            api_resource(public_base_url),
        )

    if not mcp_enabled:
        @application.api_route(
            "/mcp",
            methods=["GET", "POST", "DELETE"],
            include_in_schema=False,
            status_code=503,
        )
        def unavailable_mcp() -> dict[str, str]:
            return {"error": "MCP requires a configured database runtime"}

    return application


app = create_server()


__all__ = [
    "ProtocolApplication",
    "PROJECTION_POLL_SECONDS_ENV",
    "STATIC_DIRECTORY_ENV",
    "app",
    "create_server",
]
