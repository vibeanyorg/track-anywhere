from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.api import app, service
from track_anywhere.api_routers import auth as auth_router
from track_anywhere.security import DeploymentSecurityConfig


def _production_config() -> DeploymentSecurityConfig:
    return DeploymentSecurityConfig(
        mode="production",
        tls_enabled=True,
        key_provider_configured=True,
        backup_encryption_documented=True,
    )


def test_password_signup_requires_allowlist_outside_local_mode(monkeypatch):
    assert app is not None
    monkeypatch.setattr(service, "config", _production_config())
    monkeypatch.setattr(
        auth_router,
        "auth_settings",
        replace(auth_router.auth_settings, password_signup_allowed_emails=frozenset()),
    )
    client = TestClient(app)
    email = f"blocked-password-{uuid4().hex}@example.com"

    response = client.post(
        "/api/v1/auth/password/signup",
        json={"email": email, "password": "correct-password-123"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "password signup is not allowlisted"
    assert "set-cookie" not in response.headers


def test_password_signup_allowlist_permits_non_local_signup(monkeypatch):
    assert app is not None
    email = f"allowed-password-{uuid4().hex}@example.com"
    monkeypatch.setattr(service, "config", _production_config())
    monkeypatch.setattr(
        auth_router,
        "auth_settings",
        replace(auth_router.auth_settings, password_signup_allowed_emails=frozenset({email})),
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/auth/password/signup",
        json={"email": email, "password": "correct-password-123"},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert "secure" in response.headers["set-cookie"].lower()
