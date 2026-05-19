from __future__ import annotations

from uuid import uuid4

from .api_clients import BackendApiClient, bearer


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def issue_dev_token(client: BackendApiClient) -> str:
    response = client.post("/api/v1/auth/dev-token")
    assert response.status_code == 200
    assert response.data["token"].startswith("ta_")
    assert "account:write" in response.data["actor"]["scopes"]
    return response.data["token"]


def auth_headers(client: BackendApiClient) -> dict[str, str]:
    return bearer(issue_dev_token(client))
