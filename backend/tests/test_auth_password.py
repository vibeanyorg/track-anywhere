from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere import api_auth_runtime
from track_anywhere import password_auth
from track_anywhere.api import app, service
from track_anywhere.api_routers import auth as auth_router
from track_anywhere.security import DeploymentSecurityConfig


class _MissingPasswordAccountRepository:
    def get(self, _email):
        return None

    def create(self, **_kwargs):
        raise AssertionError("not used")


def test_missing_password_account_still_runs_dummy_hash(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(password_auth, "_verify_password", lambda _password, encoded: calls.append(encoded) or False)
    store = password_auth.PasswordAccountStore(_MissingPasswordAccountRepository())

    with pytest.raises(password_auth.PolicyDenied):
        store.authenticate(email="missing@example.com", password="incorrect-password")

    assert len(calls) == 1


def test_password_login_rate_limits_repeated_failures(monkeypatch):
    assert app is not None
    monkeypatch.setattr(password_auth, "_verify_password", lambda _password, _encoded: False)
    client = TestClient(app)
    email = f"rate-limit-{uuid4().hex}@example.com"

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/password/login",
            json={"email": email, "password": "incorrect-password"},
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/v1/auth/password/login",
        json={"email": email, "password": "incorrect-password"},
    )

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def _production_config() -> DeploymentSecurityConfig:
    return DeploymentSecurityConfig(
        mode="production",
        tls_enabled=True,
        key_provider_configured=True,
        backup_encryption_documented=True,
    )


def test_password_signup_requires_allowlist_outside_local_mode(monkeypatch):
    assert app is not None
    production_config = _production_config()
    monkeypatch.setattr(service, "config", production_config)
    monkeypatch.setattr(api_auth_runtime, "deployment_config", production_config)
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
    production_config = _production_config()
    monkeypatch.setattr(service, "config", production_config)
    monkeypatch.setattr(api_auth_runtime, "deployment_config", production_config)
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


def test_password_signup_cookie_secure_can_be_disabled_for_loopback_tunnel(monkeypatch):
    assert app is not None
    email = f"loopback-password-{uuid4().hex}@example.com"
    production_config = _production_config()
    monkeypatch.setattr(service, "config", production_config)
    monkeypatch.setattr(api_auth_runtime, "deployment_config", production_config)
    monkeypatch.setenv("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", "0")
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
    assert "secure" not in response.headers["set-cookie"].lower()
