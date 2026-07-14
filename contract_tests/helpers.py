from __future__ import annotations

from uuid import uuid4

from .api_clients import BackendApiClient, bearer


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def issue_contract_token(client: BackendApiClient) -> str:
    return client.api_key


def auth_headers(client: BackendApiClient) -> dict[str, str]:
    return {"X-API-Key": client.api_key}


def bearer_headers(client: BackendApiClient) -> dict[str, str]:
    return bearer(issue_contract_token(client))
