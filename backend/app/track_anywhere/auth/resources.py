from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

from .errors import AuthSecurityError


PUBLIC_BASE_URL_ENV = "TRACK_ANYWHERE_PUBLIC_BASE_URL"


def canonical_public_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AuthSecurityError("public base URL must be an absolute http(s) origin")
    if parsed.path not in {"", "/"}:
        raise AuthSecurityError("public base URL must not contain a path")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "testserver",
    }:
        raise AuthSecurityError("public base URL must use HTTPS outside loopback")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def configured_public_base_url(default: str = "http://127.0.0.1:8000") -> str:
    value = os.environ.get(PUBLIC_BASE_URL_ENV)
    if value is None:
        if os.environ.get("TRACK_ANYWHERE_MODE", "local") != "local":
            raise AuthSecurityError(
                "TRACK_ANYWHERE_PUBLIC_BASE_URL is required outside local mode"
            )
        value = default
    return canonical_public_base_url(value)


def api_resource(public_base_url: str) -> str:
    return f"{canonical_public_base_url(public_base_url)}/api/v2"


def mcp_resource(public_base_url: str) -> str:
    return f"{canonical_public_base_url(public_base_url)}/mcp"


def allowed_oauth_resources(public_base_url: str) -> frozenset[str]:
    return frozenset(
        {
            api_resource(public_base_url),
            mcp_resource(public_base_url),
        }
    )


def require_oauth_resource(resource: str, public_base_url: str) -> str:
    if resource not in allowed_oauth_resources(public_base_url):
        raise AuthSecurityError("resource is not served by this authorization server")
    return resource


__all__ = [
    "PUBLIC_BASE_URL_ENV",
    "allowed_oauth_resources",
    "api_resource",
    "canonical_public_base_url",
    "configured_public_base_url",
    "mcp_resource",
    "require_oauth_resource",
]
