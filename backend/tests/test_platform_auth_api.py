from __future__ import annotations

import base64
from hashlib import sha256
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service


def pkce_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_api_key_management_lists_and_revokes_without_raw_key():
    assert app is not None
    client = TestClient(app)
    issue = client.post(
        "/api/v1/credentials/agent",
        json={"scopes": ["account:read", "book:read"], "ttl_minutes": 30},
        headers={"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-key-management-issue"},
    )
    raw_key = issue.json()["credential"]["token"]

    listing = client.get("/api/v1/credentials", headers={"Authorization": f"Bearer {service.owner_token}"})
    managed = next(item for item in listing.json()["credentials"] if item["active"] and "account:read" in item["scopes"])
    revoke = client.post(
        f"/api/v1/credentials/{managed['credential_id']}/revoke",
        json={"reason": "test revoke"},
        headers={"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-key-management-revoke"},
    )
    denied = client.get("/api/v1/accounts", headers={"X-API-Key": raw_key})

    assert listing.status_code == 200
    assert raw_key not in listing.text
    assert managed["key_prefix"].startswith("ta_...")
    assert revoke.status_code == 200
    assert denied.status_code == 403


def test_platform_oauth_pkce_exchange_issues_usable_token():
    assert app is not None
    client = TestClient(app)
    session_response = client.post("/api/v1/session/dev-local")
    verifier = "a" * 64

    authorize = client.post(
        "/api/v1/oauth/authorize",
        json={
            "client_id": "track-anywhere-web",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "scope": "account:read book:read ledger:read",
            "state": "state-1",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "action": "approve",
        },
        headers={
            "X-CSRF-Token": session_response.json()["csrf_token"],
            "Origin": "http://localhost:3000",
        },
    )
    code = parse_qs(urlparse(authorize.json()["redirect_uri"]).query)["code"][0]
    token = client.post(
        "/api/v1/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "track-anywhere-web",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "code_verifier": verifier,
        },
    )
    accounts = client.get("/api/v1/accounts", headers={"X-API-Key": token.json()["access_token"]})
    replay = client.post(
        "/api/v1/oauth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "track-anywhere-web",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "code_verifier": verifier,
        },
    )

    assert authorize.status_code == 200
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"
    assert accounts.status_code == 200
    assert replay.status_code == 400
