from __future__ import annotations

from uuid import uuid4

from .api_clients import BackendApiClient


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def auth_headers(client: BackendApiClient) -> dict[str, str]:
    return {"X-API-Key": client.api_key}
