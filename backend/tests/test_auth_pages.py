from __future__ import annotations

import base64
from html import unescape
from hashlib import sha256
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app


def _pkce_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _create_device_authorization(client: TestClient, scope: str) -> dict:
    response = client.post(
        "/api/v1/oauth/device/authorize",
        json={"client_id": "track-anywhere-web", "scope": scope},
    )
    assert response.status_code == 200
    return response.json()


def test_fastapi_login_page_renders_password_form():
    assert app is not None
    response = TestClient(app).get("/api/v1/auth/login")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'action="/api/v1/auth/password/login/form"' in response.text
    assert "/api/v1/auth/signup" in response.text


def test_fastapi_password_signup_form_issues_browser_session():
    assert app is not None
    client = TestClient(app)
    email = f"fastapi-page-{uuid4().hex}@example.com"

    response = client.post(
        "/api/v1/auth/password/signup/form",
        data={
            "email": email,
            "password": "correct-password-123",
            "display_name": "FastAPI Page User",
            "next": "/api/v1/auth/session-view",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/v1/auth/session-view"
    assert "ta_session" in client.cookies
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True


def test_fastapi_cli_callback_requires_login_then_approves_code():
    assert app is not None
    client = TestClient(app)
    verifier = "c" * 64
    query = {
        "client_id": "track-anywhere-web",
        "redirect_uri": "http://127.0.0.1:8000/api/v1/auth/callback",
        "scope": "account:read book:read ledger:read",
        "state": "state-fastapi-page",
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }

    unauthenticated = client.get("/api/v1/auth/callback", params=query, follow_redirects=False)
    client.post("/api/v1/session/dev-local")
    page = client.get("/api/v1/auth/callback", params=query)
    approved = client.post(
        "/api/v1/auth/callback",
        data={**query, "action": "approve", "csrf_token": client.cookies.get("ta_csrf")},
    )

    callback_url = unescape(approved.text.split("<textarea readonly>", 1)[1].split("</textarea>", 1)[0])
    code = parse_qs(urlparse(callback_url).query)["code"][0]

    token = client.post(
        "/api/v1/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "track-anywhere-web",
            "redirect_uri": query["redirect_uri"],
            "code_verifier": verifier,
        },
    )

    assert unauthenticated.status_code == 303
    assert "/api/v1/auth/login" in unauthenticated.headers["location"]
    assert page.status_code == 200
    assert "Connect command line" in page.text
    assert approved.status_code == 200
    assert "state-fastapi-page" in callback_url
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"


def test_fastapi_cli_callback_page_can_select_available_scopes():
    assert app is not None
    client = TestClient(app)
    verifier = "d" * 64
    query = {
        "client_id": "track-anywhere-web",
        "redirect_uri": "http://127.0.0.1:8000/api/v1/auth/callback",
        "scope": "account:read book:read ledger:read",
        "state": "state-downscope",
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }

    client.post("/api/v1/session/dev-local")
    page = client.get("/api/v1/auth/callback", params=query)
    approved = client.post(
        "/api/v1/auth/callback",
        data={
            **query,
            "action": "approve",
            "csrf_token": client.cookies.get("ta_csrf"),
            "scope_selection_present": "1",
            "approved_scope": ["account:read", "account:write", "book:read"],
        },
    )

    callback_url = unescape(approved.text.split("<textarea readonly>", 1)[1].split("</textarea>", 1)[0])
    code = parse_qs(urlparse(callback_url).query)["code"][0]
    token = client.post(
        "/api/v1/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "track-anywhere-web",
            "redirect_uri": query["redirect_uri"],
            "code_verifier": verifier,
        },
    )

    assert page.status_code == 200
    assert "Token permissions" in page.text
    assert "Account permissions" in page.text
    assert "Ledger permissions" in page.text
    assert 'data-scope-group="account"' in page.text
    assert 'name="approved_scope" value="account:read"' in page.text
    assert 'name="approved_scope" value="account:write"' in page.text
    assert 'name="approved_scope" value="ledger:read"' in page.text
    assert approved.status_code == 200
    assert token.status_code == 200
    assert token.json()["scope"] == "account:read account:write book:read"


def test_fastapi_cli_callback_rejects_disallowed_scope_selection():
    assert app is not None
    client = TestClient(app)
    query = {
        "client_id": "track-anywhere-web",
        "redirect_uri": "http://127.0.0.1:8000/api/v1/auth/callback",
        "scope": "account:read",
        "state": "state-tamper",
        "code_challenge": _pkce_challenge("e" * 64),
        "code_challenge_method": "S256",
    }

    client.post("/api/v1/session/dev-local")
    approved = client.post(
        "/api/v1/auth/callback",
        data={
            **query,
            "action": "approve",
            "csrf_token": client.cookies.get("ta_csrf"),
            "scope_selection_present": "1",
            "approved_scope": ["account:read", "credential:write"],
        },
    )

    assert approved.status_code == 400
    assert "unknown OAuth scopes" in approved.text


def test_fastapi_device_page_can_select_available_scopes():
    assert app is not None
    client = TestClient(app)
    authorization = _create_device_authorization(client, "account:read book:read ledger:read")

    client.post("/api/v1/session/dev-local")
    page = client.get("/api/v1/auth/device", params={"user_code": authorization["user_code"]})
    approved = client.post(
        "/api/v1/auth/device",
        data={
            "user_code": authorization["user_code"],
            "action": "approve",
            "csrf_token": client.cookies.get("ta_csrf"),
            "scope_selection_present": "1",
            "approved_scope": ["account:read", "account:write", "book:read"],
        },
    )
    token = client.post(
        "/api/v1/oauth/token",
        json={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": authorization["device_code"],
            "client_id": "track-anywhere-web",
        },
    )

    assert page.status_code == 200
    assert "Token permissions" in page.text
    assert "Account permissions" in page.text
    assert "Ledger permissions" in page.text
    assert 'data-scope-group="account"' in page.text
    assert 'name="approved_scope" value="account:read"' in page.text
    assert 'name="approved_scope" value="account:write"' in page.text
    assert 'name="approved_scope" value="ledger:read"' in page.text
    assert approved.status_code == 200
    assert token.status_code == 200
    assert token.json()["scope"] == "account:read account:write book:read"


def test_fastapi_device_page_rejects_disallowed_scope_selection():
    assert app is not None
    client = TestClient(app)
    authorization = _create_device_authorization(client, "account:read")

    client.post("/api/v1/session/dev-local")
    approved = client.post(
        "/api/v1/auth/device",
        data={
            "user_code": authorization["user_code"],
            "action": "approve",
            "csrf_token": client.cookies.get("ta_csrf"),
            "scope_selection_present": "1",
            "approved_scope": ["account:read", "credential:write"],
        },
    )

    assert approved.status_code == 400
    assert "unknown OAuth scopes" in approved.text
