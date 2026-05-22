from __future__ import annotations

from html import unescape
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service


def _created_token(text: str) -> str:
    return unescape(text.split("<textarea readonly>", 1)[1].split("</textarea>", 1)[0])


def _credential_named(name: str) -> dict:
    return next(item for item in service.list_agent_credentials(service.owner_token) if item["name"] == name)


def test_machine_tokens_page_requires_browser_login():
    assert app is not None
    response = TestClient(app).get("/api/v1/auth/machine-tokens", follow_redirects=False)

    assert response.status_code == 303
    assert "/api/v1/auth/login" in response.headers["location"]


def test_machine_tokens_page_renders_grouped_scope_picker():
    assert app is not None
    client = TestClient(app)
    client.post("/api/v1/session/dev-local")

    page = client.get("/api/v1/auth/machine-tokens")

    assert page.status_code == 200
    assert "Machine tokens" in page.text
    assert "All available permissions" in page.text
    assert "Account permissions" in page.text
    assert "Ledger permissions" in page.text
    assert 'name="approved_scope" value="account:write"' in page.text


def test_machine_token_page_creates_usable_one_time_token():
    assert app is not None
    client = TestClient(app)
    token_name = f"local-agent-{uuid4().hex}"
    client.post("/api/v1/session/dev-local")

    created = client.post(
        "/api/v1/auth/machine-tokens",
        data={
            "csrf_token": client.cookies.get("ta_csrf"),
            "scope_selection_present": "1",
            "name": token_name,
            "description": "pytest m2m token",
            "ttl_days": "7",
            "approved_scope": ["account:read", "book:read", "ledger:read"],
        },
    )
    raw_token = _created_token(created.text)
    accounts = client.get("/api/v1/accounts", headers={"X-API-Key": raw_token})
    credential = _credential_named(token_name)

    assert created.status_code == 200
    assert raw_token.startswith("ta_m2m_")
    assert "Shown once" in created.text
    assert accounts.status_code == 200
    assert credential["actor_type"] == "machine"
    assert credential["auth_kind"] == "m2m"
    assert credential["scopes"] == ["account:read", "book:read", "ledger:read"]
    assert credential["active"] is True


def test_machine_token_page_revokes_token_by_id():
    assert app is not None
    client = TestClient(app)
    token_name = f"revoked-agent-{uuid4().hex}"
    client.post("/api/v1/session/dev-local")
    client.post(
        "/api/v1/auth/machine-tokens",
        data={
            "csrf_token": client.cookies.get("ta_csrf"),
            "scope_selection_present": "1",
            "name": token_name,
            "ttl_days": "3",
            "approved_scope": ["account:read"],
        },
    )
    credential_id = _credential_named(token_name)["credential_id"]

    revoked = client.post(
        f"/api/v1/auth/machine-tokens/{credential_id}/revoke",
        data={"csrf_token": client.cookies.get("ta_csrf")},
        follow_redirects=False,
    )
    credential = _credential_named(token_name)

    assert revoked.status_code == 303
    assert revoked.headers["location"] == "/api/v1/auth/machine-tokens"
    assert credential["active"] is False
