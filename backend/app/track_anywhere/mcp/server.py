from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool as McpTool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..api.dependencies import RuntimeDependencies
from ..auth.resources import canonical_public_base_url, mcp_resource
from ..auth.security import (
    protected_resource_metadata,
    protected_resource_metadata_url,
)
from .auth import DatabaseTokenVerifier, MCP_REQUIRED_SCOPE, MCP_WRITE_SCOPE
from .tools import register_ledger_tools


MCP_TRUSTED_PROXY_HOSTS_ENV = "TRACK_ANYWHERE_MCP_TRUSTED_PROXY_HOSTS"


class ChatGptFastMCP(FastMCP):
    write_scope_resource_metadata_url: str | None = None

    async def list_tools(self) -> list[McpTool]:
        tools = await super().list_tools()
        result: list[McpTool] = []
        for tool in tools:
            schemes = (tool.meta or {}).get("securitySchemes")
            result.append(
                tool.model_copy(update={"securitySchemes": schemes})
                if schemes is not None
                else tool
            )
        return result

    async def call_tool(self, name: str, arguments: dict[str, object]):
        try:
            return await super().call_tool(name, arguments)
        except ToolError as error:
            message = str(error)
            metadata_url = self.write_scope_resource_metadata_url
            if "needs ledger:write" not in message or metadata_url is None:
                raise
            challenge = (
                f'Bearer resource_metadata="{metadata_url}", '
                'error="insufficient_scope", '
                f'scope="{MCP_REQUIRED_SCOPE} {MCP_WRITE_SCOPE}"'
            )
            return CallToolResult(
                content=[TextContent(type="text", text=message)],
                isError=True,
                _meta={"mcp/www_authenticate": [challenge]},
            )


@dataclass(frozen=True, slots=True)
class McpRuntime:
    server: ChatGptFastMCP
    application: Starlette
    resource: str
    protected_resource_metadata_path: str


def create_mcp_runtime(
    dependencies: RuntimeDependencies,
    public_base_url: str,
) -> McpRuntime:
    base = canonical_public_base_url(public_base_url)
    resource = mcp_resource(base)
    parsed = urlparse(base)
    public_host = parsed.netloc
    server = ChatGptFastMCP(
        name="Track Anywhere Ledger",
        instructions=(
            "Read and, only after explicit user confirmation, record verified Track "
            "Anywhere V2 ledger data. Integer units are exact; use asset scale "
            "metadata before presenting decimal amounts."
        ),
        token_verifier=DatabaseTokenVerifier(
            dependencies.session_factory,
            resource,
        ),
        auth=AuthSettings(
            issuer_url=base,
            resource_server_url=resource,
            required_scopes=[MCP_REQUIRED_SCOPE],
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(
                dict.fromkeys(
                    [
                        public_host,
                        *_trusted_proxy_hosts(),
                        "127.0.0.1:*",
                        "localhost:*",
                        "[::1]:*",
                        "testserver",
                    ]
                )
            ),
            allowed_origins=list(
                dict.fromkeys(
                    [
                        base,
                        "http://127.0.0.1:*",
                        "http://localhost:*",
                        "http://[::1]:*",
                        "http://testserver",
                    ]
                )
            ),
        ),
    )
    server.write_scope_resource_metadata_url = protected_resource_metadata_url(
        resource
    )
    register_ledger_tools(server, dependencies)
    application = server.streamable_http_app()
    _advertise_mcp_scopes(application, base, resource)
    return McpRuntime(
        server=server,
        application=application,
        resource=resource,
        protected_resource_metadata_path="/.well-known/oauth-protected-resource/mcp",
    )


def _advertise_mcp_scopes(
    application: Starlette,
    public_base_url: str,
    resource: str,
) -> None:
    path = "/.well-known/oauth-protected-resource/mcp"

    async def metadata(_request) -> JSONResponse:
        return JSONResponse(
            protected_resource_metadata(
                public_base_url,
                resource,
                scopes=(MCP_REQUIRED_SCOPE, MCP_WRITE_SCOPE),
            )
        )

    application.router.routes = [
        Route(path, endpoint=metadata, methods=["GET"]),
        *[
            route
            for route in application.router.routes
            if getattr(route, "path", None) != path
        ],
    ]


def _trusted_proxy_hosts() -> tuple[str, ...]:
    values: list[str] = []
    for raw_value in os.environ.get(MCP_TRUSTED_PROXY_HOSTS_ENV, "").split(","):
        value = raw_value.strip()
        if not value:
            continue
        if value != raw_value or any(character in value for character in "/@?#"):
            raise ValueError(
                f"{MCP_TRUSTED_PROXY_HOSTS_ENV} must contain comma-separated Host values"
            )
        values.append(value)
    return tuple(dict.fromkeys(values))


__all__ = [
    "ChatGptFastMCP",
    "MCP_TRUSTED_PROXY_HOSTS_ENV",
    "McpRuntime",
    "create_mcp_runtime",
]
