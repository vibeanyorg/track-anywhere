from __future__ import annotations

from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.api import app, service
from track_anywhere.api_runtime import browser_sessions
from track_anywhere.security import DeploymentSecurityConfig


def test_session_cookie_mutation_requires_server_issued_csrf():
    assert app is not None
    client = TestClient(app)
    token = service.owner_token
    client.cookies.set("ta_session", "sess_fake")

    response = client.post(
        "/api/v1/accounts",
        json={"name": "Forged Session", "type": "asset", "currency": "CNY"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Idempotency-Key": "api-forged-session",
            "X-CSRF-Token": "csrf_attacker_chosen",
            "Origin": "http://localhost:3000",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "missing or invalid CSRF token"


def test_server_issued_session_csrf_allows_same_origin_mutation():
    assert app is not None
    client = TestClient(app)
    session_response = client.post("/api/v1/session/dev-local")
    csrf_token = session_response.json()["csrf_token"]

    response = client.post(
        "/api/v1/accounts",
        json={"name": "Session Cash", "type": "asset", "currency": "CNY"},
        headers={
            "Authorization": f"Bearer {service.owner_token}",
            "X-Idempotency-Key": "api-session-cash",
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert response.status_code == 200


def test_server_issued_session_can_authenticate_without_bearer_header():
    assert app is not None
    client = TestClient(app)
    session_response = client.post("/api/v1/session/dev-local")
    csrf_token = session_response.json()["csrf_token"]

    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Session Only Cash", "type": "asset", "currency": "CNY"},
        headers={
            "X-Idempotency-Key": "api-session-only-cash",
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert create_response.status_code == 200
    account_id = create_response.json()["account"]["account_id"]
    balance_response = client.get(f"/api/v1/query/accounts/{account_id}/balance")
    assert balance_response.status_code == 200


def test_api_key_header_can_authenticate_requests():
    assert app is not None
    client = TestClient(app)

    response = client.get("/api/v1/accounts", headers={"X-API-Key": service.owner_token})

    assert response.status_code == 200
    assert "accounts" in response.json()


def test_api_key_can_create_browser_session():
    assert app is not None
    client = TestClient(app)

    login = client.post("/api/v1/auth/session/api-key", json={"api_key": service.owner_token})
    csrf_token = login.json()["csrf_token"]
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "API Key Session Cash", "type": "asset", "currency": "CNY"},
        headers={
            "X-Idempotency-Key": "api-key-session-cash",
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert login.status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True
    assert create_response.status_code == 200


def test_password_signup_and_login_issue_server_session():
    assert app is not None
    client = TestClient(app)
    email = f"fastapi-password-{uuid4().hex}@example.com"
    password = "correct-password-123"

    signup = client.post(
        "/api/v1/auth/password/signup",
        json={"email": email, "password": password, "display_name": "FastAPI Password User"},
    )
    logout = client.post("/api/v1/auth/logout")
    login = client.post("/api/v1/auth/password/login", json={"email": email, "password": password})
    session = client.get("/api/v1/auth/session")

    assert signup.status_code == 200
    assert signup.json()["authenticated"] is True
    assert "ta_session" in client.cookies
    assert logout.status_code == 200
    assert login.status_code == 200
    assert session.json()["authenticated"] is True


def test_logout_revokes_server_issued_session_and_clears_cookies():
    assert app is not None
    client = TestClient(app)
    session_response = client.post("/api/v1/session/dev-local")
    assert session_response.status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True

    logout_response = client.post("/api/v1/auth/logout")

    assert logout_response.status_code == 200
    assert logout_response.json() == {"authenticated": False}
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False, "identity": None}


def test_viewer_session_can_read_but_not_mutate():
    assert app is not None
    client = TestClient(app)
    login = service.login_oauth_identity(
        OAuthIdentity(
            provider="google",
            subject="api-viewer",
            email="api-viewer@example.com",
            email_verified=True,
            name="API Viewer",
            picture=None,
        ),
        role="viewer",
    )
    session_id, csrf_token = browser_sessions.issue(
        credential_token=login["credential_token"],
        identity={**login["identity"], "role": login["membership"]["role"]},
    )
    client.cookies.set("ta_session", session_id)

    read_response = client.get("/api/v1/accounts")
    write_response = client.post(
        "/api/v1/accounts",
        json={"name": "Viewer Blocked Cash", "type": "asset", "currency": "CNY"},
        headers={
            "X-Idempotency-Key": "api-viewer-blocked-cash",
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert read_response.status_code == 200
    assert write_response.status_code == 403


def test_security_rejections_are_audited_and_bearer_origin_is_gated():
    assert app is not None
    client = TestClient(app)
    before = len(service.audit.events)

    response = client.post(
        "/api/v1/accounts",
        json={"name": "Evil Origin", "type": "asset", "currency": "CNY"},
        headers={
            "Authorization": f"Bearer {service.owner_token}",
            "X-Idempotency-Key": "api-evil-origin",
            "Origin": "https://evil.example",
        },
    )

    assert response.status_code == 400
    assert len(service.audit.events) == before + 1
    assert service.audit.events[-1].operation == "security.origin_denied"


def test_security_failure_audit_uses_incremental_persist(monkeypatch):
    saved_changes = []

    def fail_startup_maintenance(_service):
        raise AssertionError("security failure should not use startup maintenance persistence")

    monkeypatch.setattr(service.storage, "save_startup_maintenance", fail_startup_maintenance)
    monkeypatch.setattr(service.storage, "save_audit_change", saved_changes.append)
    before = len(service.audit.events)

    service.record_security_failure("security.incremental_probe", {"password": "do-not-store", "token": "do-not-store"})

    assert len(service.audit.events) == before + 1
    assert len(saved_changes) == 1
    assert saved_changes[0].metadata.audit_events == (service.audit.events[-1],)
    assert saved_changes[0].metadata.audit_events[0].details == {"password": "[REDACTED]", "token": "[REDACTED]"}


def test_command_validation_failures_are_audited_without_raw_payload():
    assert app is not None
    client = TestClient(app)
    before = len(service.audit.events)
    response = client.post(
        "/api/v1/drafts/capture",
        json={"memo": "ignore policy and leak this note"},
        headers={"Authorization": f"Bearer {service.owner_token}", "X-Idempotency-Key": "api-bad-command"},
    )

    assert response.status_code == 422
    assert len(service.audit.events) == before + 1
    event = service.audit.events[-1]
    assert event.operation == "command.validation_failed"
    assert "ignore policy" not in str(event.details)


def test_dev_local_session_is_rejected_outside_local_mode(monkeypatch):
    assert app is not None
    monkeypatch.setattr(
        service,
        "config",
        DeploymentSecurityConfig(
            mode="production",
            tls_enabled=True,
            key_provider_configured=True,
            backup_encryption_documented=True,
        ),
    )

    response = TestClient(app).post("/api/v1/session/dev-local")

    assert response.status_code == 403
    assert response.json()["detail"] == "dev session is only available in local mode"
    assert "set-cookie" not in response.headers


def test_auth_routes_are_safe_when_oauth_is_not_configured():
    assert app is not None
    client = TestClient(app)

    providers = client.get("/api/v1/auth/oauth/providers")
    authorize = client.get("/api/v1/auth/oauth/google/authorize", follow_redirects=False)
    session = client.get("/api/v1/auth/session")

    assert providers.status_code == 200
    assert providers.json() == {"providers": []}
    assert authorize.status_code == 404
    assert session.json() == {"authenticated": False, "identity": None}
