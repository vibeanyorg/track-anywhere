from __future__ import annotations

from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.api import app, service
from track_anywhere.api_browser_sessions import browser_sessions
from track_anywhere.security import DeploymentSecurityConfig
from track_anywhere.service import FinanceService


def test_backoffice_read_models_are_available_from_fastapi():
    assert app is not None
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {service.owner_token}"}
    suffix = uuid4().hex

    account_response = client.post(
        "/api/v1/accounts",
        json={
            "name": f"Backoffice Cash {suffix}",
            "type": "asset",
            "currency": "CNY",
            "opening_balance": "88",
        },
        headers={**headers, "X-Idempotency-Key": f"backoffice-account-{suffix}"},
    )
    category_parent_response = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": f"Backoffice {suffix}"},
        headers={**headers, "X-Idempotency-Key": f"backoffice-category-parent-{suffix}"},
    )
    assert category_parent_response.status_code == 200
    category_response = client.post(
        "/api/v1/categories",
        json={"kind": "expense", "name": "Coffee", "parent_id": category_parent_response.json()["category"]["category_id"]},
        headers={**headers, "X-Idempotency-Key": f"backoffice-category-{suffix}"},
    )
    assert account_response.status_code == 200
    assert category_response.status_code == 200

    account_id = account_response.json()["account"]["account_id"]
    category_id = category_response.json()["category"]["category_id"]
    service.login_oauth_identity(
        OAuthIdentity(
            provider="google",
            subject=f"backoffice-{suffix}",
            email=f"backoffice-{suffix}@example.com",
            email_verified=True,
            name="Backoffice User",
            picture=None,
        ),
        role="viewer",
    )

    accounts = client.get(f"/api/v1/backoffice/accounts?search={suffix}", headers=headers)
    categories = client.get(f"/api/v1/backoffice/categories?search={suffix}", headers=headers)
    books = client.get("/api/v1/backoffice/books", headers=headers)
    members = client.get("/api/v1/backoffice/book-members", headers=headers)
    users = client.get("/api/v1/backoffice/ledger-users", headers=headers)
    identities = client.get(f"/api/v1/backoffice/auth-identities?search={suffix}", headers=headers)
    transactions = client.get(f"/api/v1/backoffice/transactions?search=Backoffice Cash {suffix}", headers=headers)
    audit_events = client.get("/api/v1/backoffice/audit-events?ordering=-created_at", headers=headers)
    roles = client.get("/api/v1/backoffice/roles", headers=headers)

    assert accounts.status_code == 200
    assert categories.status_code == 200
    assert books.status_code == 200
    assert members.status_code == 200
    assert users.status_code == 200
    assert identities.status_code == 200
    assert transactions.status_code == 200
    assert audit_events.status_code == 200
    assert roles.status_code == 200
    assert account_id in [item["account_id"] for item in accounts.json()]
    assert category_id in [item["category_id"] for item in categories.json()]
    assert any(item["book_id"] == "book_default" for item in books.json())
    assert any(item["role"] == "owner" for item in members.json())
    assert any(item["email"] == f"backoffice-{suffix}@example.com" for item in identities.json())
    assert any(item["operation"] == "account.create" for item in audit_events.json())
    assert any(item["role"] == "viewer" and "account:read" in item["scopes"] for item in roles.json())
    assert any(item["transaction_id"] for item in transactions.json())


def test_backoffice_requires_admin_or_owner_scope():
    assert app is not None
    client = TestClient(app)
    login = service.login_oauth_identity(
        OAuthIdentity(
            provider="google",
            subject=f"backoffice-viewer-{uuid4().hex}",
            email="backoffice-viewer@example.com",
            email_verified=True,
            name="Backoffice Viewer",
            picture=None,
        ),
        role="viewer",
    )
    session_id, _csrf_token = browser_sessions.issue(
        credential_token=login["credential_token"],
        identity={**login["identity"], "role": login["membership"]["role"]},
    )
    client.cookies.set("ta_session", session_id)

    response = client.get("/api/v1/backoffice/accounts")

    assert response.status_code == 403
    assert "user:write" in response.json()["detail"]


def test_password_accounts_persist_across_store_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'track-anywhere.sqlite3'}"
    first_service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    email = f"persist-password-{uuid4().hex}@example.com"

    created = first_service.create_password_account(
        email=email,
        password="correct-password-123",
        display_name="Persisted Password",
        signup_allowed_emails=frozenset(),
    )

    second_service = FinanceService(DeploymentSecurityConfig(), database_url=database_url)
    authenticated = second_service.authenticate_password_account(email=email, password="correct-password-123")

    assert created.email == email
    assert authenticated.email == email
    assert authenticated.display_name == "Persisted Password"
    assert authenticated.role == "owner"
