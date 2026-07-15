from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

from .api.app import create_app as create_api_app
from .api.dependencies import RuntimeDependencies
from .auth.resources import api_resource, configured_public_base_url
from .auth.security import authorization_server_metadata, protected_resource_metadata
from .mcp.server import McpRuntime, create_mcp_runtime


class ProtocolApplication:
    def __init__(
        self,
        *,
        rest_application: FastAPI,
        discovery_application: FastAPI,
        mcp_runtime: McpRuntime | None,
    ) -> None:
        self.rest_application = rest_application
        self.discovery_application = discovery_application
        self.mcp_runtime = mcp_runtime
        self.state = rest_application.state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            if self.mcp_runtime is None:
                await self.rest_application(scope, receive, send)
                return
            async with self.mcp_runtime.server.session_manager.run():
                await self.rest_application(scope, receive, send)
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
        await self.rest_application(scope, receive, send)


def create_server(
    *,
    dependencies: RuntimeDependencies | None = None,
    public_base_url: str | None = None,
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
    discovery = _create_discovery_application(base, mcp_runtime is not None)
    return ProtocolApplication(
        rest_application=rest,
        discovery_application=discovery,
        mcp_runtime=mcp_runtime,
    )


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


__all__ = ["ProtocolApplication", "app", "create_server"]
