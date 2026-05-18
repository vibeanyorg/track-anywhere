from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
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


def test_session_cookie_secure_flag_tracks_deployment_mode(monkeypatch):
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

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()
