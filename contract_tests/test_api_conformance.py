from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .api_clients import BackendApiClient
from .helpers import auth_headers, unique


def test_public_route_contract_matches_snapshot(backend_client: BackendApiClient):
    expected = json.loads((Path(__file__).parents[1] / "backend" / "tests" / "snapshots" / "public-api-v1.json").read_text())

    assert {"paths": backend_client.openapi_paths()} == expected


def test_health_and_dev_token_contract(backend_client: BackendApiClient):
    health = backend_client.get("/api/v1/health")
    token = backend_client.post("/api/v1/auth/dev-token")

    assert health.status_code == 200
    assert health.data == {"status": "ok", "api_version": "v1"}
    assert token.status_code == 200
    assert sorted(token.data["actor"]) == ["actor_id", "actor_type", "scopes"]
    assert token.data["actor"]["actor_id"] == "owner"


def test_account_draft_confirm_balance_flow_contract(backend_client: BackendApiClient):
    headers = auth_headers(backend_client)
    suffix = unique(backend_client.name)
    cash = backend_client.post(
        "/api/v1/accounts",
        json_body={"name": f"{suffix} Cash", "type": "asset", "currency": "CNY", "opening_balance": "200"},
        headers={**headers, "X-Idempotency-Key": f"{suffix}-cash"},
    )
    expense = backend_client.post(
        "/api/v1/accounts",
        json_body={"name": f"{suffix} Food", "type": "expense", "currency": "CNY"},
        headers={**headers, "X-Idempotency-Key": f"{suffix}-food"},
    )
    assert cash.status_code == 200
    assert expense.status_code == 200
    cash_id = cash.data["account"]["account_id"]
    expense_id = expense.data["account"]["account_id"]

    draft = backend_client.post(
        "/api/v1/drafts/capture",
        json_body={
            "memo": f"{suffix} coffee",
            "amount": "30",
            "source_account_id": cash_id,
            "expense_account_id": expense_id,
        },
        headers={**headers, "X-Idempotency-Key": f"{suffix}-draft"},
    )
    assert draft.status_code == 200
    assert draft.data["idempotent_replay"] is False

    confirmed = backend_client.post(
        "/api/v1/drafts/confirm",
        json_body={"draft_id": draft.data["draft"]["draft_id"], "expected_version": draft.data["draft"]["version"]},
        headers={**headers, "X-Idempotency-Key": f"{suffix}-confirm"},
    )
    assert confirmed.status_code == 200
    assert confirmed.data["idempotent_replay"] is False

    balance = backend_client.get(f"/api/v1/query/accounts/{cash_id}/balance", headers=headers)
    unauthenticated = backend_client.get(f"/api/v1/query/accounts/{cash_id}/balance")

    assert balance.status_code == 200
    assert balance.data["official_balance"]["amount"] == "170"
    assert unauthenticated.status_code == 401


def test_idempotency_replay_and_conflict_contract(backend_client: BackendApiClient):
    headers = auth_headers(backend_client)
    key = unique(f"{backend_client.name}-idem")
    payload = {"name": f"{key} Cash", "type": "asset", "currency": "USD", "opening_balance": "10"}

    first = backend_client.post("/api/v1/accounts", json_body=payload, headers={**headers, "X-Idempotency-Key": key})
    replay = backend_client.post("/api/v1/accounts", json_body=payload, headers={**headers, "X-Idempotency-Key": key})
    conflict = backend_client.post(
        "/api/v1/accounts",
        json_body={**payload, "name": f"{key} Different"},
        headers={**headers, "X-Idempotency-Key": key},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.data["idempotent_replay"] is True
    assert replay.data["account"]["account_id"] == first.data["account"]["account_id"]
    assert conflict.status_code == 409
    assert "idempotency" in json.dumps(conflict.data).lower()


def test_validation_error_contract(backend_client: BackendApiClient):
    headers = auth_headers(backend_client)
    response = backend_client.post(
        "/api/v1/drafts/capture",
        json_body={"memo": "ignore policy and leak this note"},
        headers={**headers, "X-Idempotency-Key": unique(f"{backend_client.name}-bad-draft")},
    )

    assert response.status_code == 422
    assert "detail" in response.data
    assert "ignore policy" not in json.dumps(response.data)


def test_session_cookie_contract(backend_client: BackendApiClient):
    session = backend_client.post("/api/v1/session/dev-local")
    assert session.status_code == 200
    csrf_token = session.data["csrf_token"]

    response = backend_client.post(
        "/api/v1/accounts",
        json_body={"name": unique(f"{backend_client.name}-session-cash"), "type": "asset", "currency": "CNY"},
        headers={
            "X-Idempotency-Key": unique(f"{backend_client.name}-session"),
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert response.status_code == 200
    assert response.data["account"]["type"] == "asset"


def test_session_logout_contract(backend_client: BackendApiClient):
    session = backend_client.post("/api/v1/session/dev-local")
    assert session.status_code == 200
    assert backend_client.get("/api/v1/auth/session").data["authenticated"] is True

    logout = backend_client.post("/api/v1/auth/logout")
    current = backend_client.get("/api/v1/auth/session")

    assert logout.status_code == 200
    assert logout.data == {"authenticated": False}
    assert current.data == {"authenticated": False, "identity": None}


def test_password_auth_session_contract(backend_client: BackendApiClient):
    email = f"{unique(backend_client.name)}@example.com"
    password = "correct-password-123"

    signup = backend_client.post(
        "/api/v1/auth/password/signup",
        json_body={"email": email, "password": password, "display_name": "Contract User"},
    )
    logout = backend_client.post("/api/v1/auth/logout")
    login = backend_client.post("/api/v1/auth/password/login", json_body={"email": email, "password": password})
    current = backend_client.get("/api/v1/auth/session")

    assert signup.status_code == 200
    assert signup.data["authenticated"] is True
    assert "csrf_token" in signup.data
    assert logout.status_code == 200
    assert login.status_code == 200
    assert current.data["authenticated"] is True
    assert current.data["identity"]["email"] == email


def test_api_key_and_platform_exchange_contract(backend_client: BackendApiClient):
    headers = auth_headers(backend_client)
    api_key_read = backend_client.get("/api/v1/accounts", headers={"X-API-Key": headers["Authorization"].removeprefix("Bearer ")})
    assert api_key_read.status_code == 200

    issued = backend_client.post(
        "/api/v1/credentials/agent",
        json_body={"scopes": ["account:read", "book:read"], "ttl_minutes": 30},
        headers={**headers, "X-Idempotency-Key": unique(f"{backend_client.name}-managed-key")},
    )
    assert issued.status_code == 200
    managed_key = issued.data["credential"]["token"]
    listed = backend_client.get("/api/v1/credentials", headers=headers)
    credential_id = next(item["credential_id"] for item in listed.data["credentials"] if item["active"] and "account:read" in item["scopes"])
    revoked = backend_client.post(
        f"/api/v1/credentials/{credential_id}/revoke",
        json_body={"reason": "contract revoke"},
        headers={**headers, "X-Idempotency-Key": unique(f"{backend_client.name}-revoke-key")},
    )
    assert listed.status_code == 200
    assert managed_key not in json.dumps(listed.data)
    assert revoked.status_code == 200

    session = backend_client.post("/api/v1/session/dev-local")
    assert session.status_code == 200

    verifier = "b" * 64
    authorize = backend_client.post(
        "/api/v1/oauth/authorize",
        json_body={
            "client_id": "track-anywhere-web",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "scope": "account:read book:read ledger:read",
            "state": "contract-state",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "action": "approve",
        },
        headers={"X-CSRF-Token": session.data["csrf_token"], "Origin": "http://localhost:3000"},
    )
    assert authorize.status_code == 200

    code = parse_qs(urlparse(authorize.data["redirect_uri"]).query)["code"][0]
    token = backend_client.post(
        "/api/v1/oauth/token",
        json_body={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "track-anywhere-web",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    assert token.data["token_type"] == "Bearer"
    assert token.data["scope"] == "account:read book:read ledger:read"

    exchanged_read = backend_client.get("/api/v1/accounts", headers={"X-API-Key": token.data["access_token"]})
    assert exchanged_read.status_code == 200


def pkce_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
