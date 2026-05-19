from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.db import connection
from django.test import Client

import backend_django.track_anywhere_django.allauth_adapter as allauth_adapter
from backend_django.track_anywhere_django.api import api, service
from backend_django.track_anywhere_django.models import Account, AuditEvent, AuthIdentity, LedgerBook, Transaction
from backend_django.track_anywhere_django.roles import ROLE_GROUPS, ensure_role_groups, ensure_user_role, grant_book_role


def _json(response):
    return json.loads(response.content.decode("utf-8"))


def _post(client: Client, path: str, payload: dict, headers: dict[str, str]):
    return client.post(path, payload, content_type="application/json", headers=headers)


def _ensure_unmanaged_tables(*models) -> None:
    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as schema_editor:
        for model in models:
            if model._meta.db_table not in existing:
                schema_editor.create_model(model)
                existing.add(model._meta.db_table)


def _bridge_session(client: Client) -> dict:
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    data = _json(response)
    assert data["authenticated"] is True
    assert "ta_session" in client.cookies
    assert "ta_csrf" in client.cookies
    return data


def test_django_api_v1_route_contract_matches_fastapi_snapshot():
    actual = {
        "paths": {
            path: sorted(method for method in details if method in {"get", "post", "put", "patch", "delete"})
            for path, details in sorted(api.get_openapi_schema()["paths"].items())
        }
    }
    expected = json.loads((Path(__file__).parents[2] / "backend" / "tests" / "snapshots" / "public-api-v1.json").read_text())

    assert actual == expected


def test_django_api_capture_confirm_and_query_flow():
    client = Client()
    token = service.owner_token
    headers = {"Authorization": f"Bearer {token}"}

    cash_resp = _post(
        client,
        "/api/v1/accounts",
        {"name": "Django Cash", "type": "asset", "currency": "CNY", "opening_balance": "200"},
        {**headers, "X-Idempotency-Key": "django-cash"},
    )
    assert cash_resp.status_code == 200
    cash_id = _json(cash_resp)["account"]["account_id"]

    expense_resp = _post(
        client,
        "/api/v1/accounts",
        {"name": "Django Food", "type": "expense", "currency": "CNY"},
        {**headers, "X-Idempotency-Key": "django-food"},
    )
    assert expense_resp.status_code == 200
    expense_id = _json(expense_resp)["account"]["account_id"]

    draft_resp = _post(
        client,
        "/api/v1/drafts/capture",
        {
            "memo": "Django coffee",
            "amount": "30",
            "source_account_id": cash_id,
            "expense_account_id": expense_id,
        },
        {**headers, "X-Idempotency-Key": "django-draft"},
    )
    assert draft_resp.status_code == 200
    draft = _json(draft_resp)["draft"]

    confirm_resp = _post(
        client,
        "/api/v1/drafts/confirm",
        {"draft_id": draft["draft_id"], "expected_version": draft["version"]},
        {**headers, "X-Idempotency-Key": "django-confirm"},
    )
    assert confirm_resp.status_code == 200

    balance_resp = client.get(f"/api/v1/query/accounts/{cash_id}/balance", headers=headers)
    assert balance_resp.status_code == 200
    assert _json(balance_resp)["official_balance"]["amount"] == "170"

    unauthenticated_balance = client.get(f"/api/v1/query/accounts/{cash_id}/balance")
    assert unauthenticated_balance.status_code == 401


def test_django_session_cookie_can_authenticate_without_bearer_header():
    client = Client()
    session_response = client.post("/api/v1/session/dev-local")
    csrf_token = _json(session_response)["csrf_token"]

    create_response = _post(
        client,
        "/api/v1/accounts",
        {"name": "Django Session Cash", "type": "asset", "currency": "CNY"},
        {
            "X-Idempotency-Key": "django-session-cash",
            "X-CSRF-Token": csrf_token,
            "Origin": "http://localhost:3000",
        },
    )

    assert create_response.status_code == 200
    account_id = _json(create_response)["account"]["account_id"]
    balance_response = client.get(f"/api/v1/query/accounts/{account_id}/balance")
    assert balance_response.status_code == 200


def test_django_admin_registers_core_ledger_preview_models():
    from django.contrib import admin

    for model in (LedgerBook, Account, Transaction, AuthIdentity, AuditEvent):
        assert model in admin.site._registry


def test_django_ecosystem_roles_and_guardian_backoffice_access():
    _ensure_unmanaged_tables(LedgerBook, Account)
    ensure_role_groups()
    assert set(ROLE_GROUPS.values()).issubset(set(Group.objects.values_list("name", flat=True)))

    user = get_user_model().objects.create_user(username="viewer", email="viewer@example.com", password="secret")
    book = LedgerBook.objects.create(
        book_id="book_guardian",
        name="Guardian Book",
        kind="personal",
        base_currency="CNY",
        timezone="Asia/Shanghai",
        status="active",
        template_key=None,
        settings={},
        created_by="owner",
        version=1,
    )
    Account.objects.create(
        account_id="acc_guardian",
        book_id=book.book_id,
        name="Guardian Cash",
        type="asset",
        currency="CNY",
        version=1,
    )
    grant_book_role(user, book, "viewer")

    client = Client()
    client.force_login(user)

    books = client.get("/api/v1/backoffice/books/")
    accounts = client.get("/api/v1/backoffice/accounts/")

    assert books.status_code == 200
    assert accounts.status_code == 200
    assert [item["book_id"] for item in _json(books)] == ["book_guardian"]
    assert [item["account_id"] for item in _json(accounts)] == ["acc_guardian"]


def test_django_session_user_can_use_existing_api_contract():
    user = get_user_model().objects.create_user(username="api-user", email="api-user@example.com", password="secret")
    ensure_user_role(user, "editor")
    client = Client()
    client.force_login(user)
    session = _bridge_session(client)
    csrf_token = session["csrf_token"]

    response = _post(
        client,
        "/api/v1/accounts",
        {"name": "Django Auth Cash", "type": "asset", "currency": "CNY"},
        {"X-Idempotency-Key": "django-auth-cash", "X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert _json(response)["account"]["name"] == "Django Auth Cash"


def test_django_auth_session_bridge_sets_track_anywhere_cookies_and_requires_csrf():
    user = get_user_model().objects.create_user(username="csrf-user", email="csrf-user@example.com", password="secret")
    ensure_user_role(user, "editor")
    client = Client()
    client.force_login(user)

    session = _bridge_session(client)
    assert session["identity"]["provider"] == "django"
    assert session["identity"]["role"] == "editor"

    blocked = _post(
        client,
        "/api/v1/accounts",
        {"name": "Missing CSRF", "type": "asset", "currency": "CNY"},
        {"X-Idempotency-Key": "django-missing-csrf", "Origin": "http://localhost:3000"},
    )
    allowed = _post(
        client,
        "/api/v1/accounts",
        {"name": "With CSRF", "type": "asset", "currency": "CNY"},
        {
            "X-Idempotency-Key": "django-with-csrf",
            "X-CSRF-Token": client.cookies["ta_csrf"].value,
            "Origin": "http://localhost:3000",
        },
    )

    assert blocked.status_code == 400
    assert _json(blocked)["detail"] == "missing or invalid CSRF token"
    assert allowed.status_code == 200


def test_django_password_signup_and_login_issue_track_anywhere_session():
    client = Client()
    email = f"django-password-{uuid4().hex}@example.com"
    password = "correct-password-123"

    signup = _post(
        client,
        "/api/v1/auth/password/signup",
        {"email": email, "password": password, "display_name": "Django Password User"},
        {},
    )
    logout = client.post("/api/v1/auth/logout")
    login = _post(client, "/api/v1/auth/password/login", {"email": email, "password": password}, {})
    session = client.get("/api/v1/auth/session")

    assert signup.status_code == 200
    assert _json(signup)["authenticated"] is True
    assert "ta_session" in client.cookies
    assert "ta_csrf" in client.cookies
    assert logout.status_code == 200
    assert login.status_code == 200
    assert _json(session)["authenticated"] is True
    assert _json(session)["identity"]["email"] == email


def test_django_auth_bridge_refreshes_role_when_group_membership_changes():
    user = get_user_model().objects.create_user(username="role-user", email="role-user@example.com", password="secret")
    ensure_user_role(user, "editor")
    client = Client()
    client.force_login(user)
    _bridge_session(client)

    user.groups.clear()
    denied = _post(
        client,
        "/api/v1/accounts",
        {"name": "Downgraded Cash", "type": "asset", "currency": "CNY"},
        {
            "X-Idempotency-Key": "django-downgraded-cash",
            "X-CSRF-Token": client.cookies["ta_csrf"].value,
            "Origin": "http://localhost:3000",
        },
    )

    assert denied.status_code == 403
    assert "account:write" in _json(denied)["detail"]


def test_django_social_account_identity_is_bridged_to_track_anywhere_identity():
    user = get_user_model().objects.create_user(username="google-user", email="google-user@example.com", password="secret")
    ensure_user_role(user, "viewer")
    SocialAccount.objects.create(
        user=user,
        provider="google",
        uid="google-123",
        extra_data={"email": "google-user@example.com", "email_verified": True, "name": "Google User", "picture": "https://example.com/a.png"},
    )
    client = Client()
    client.force_login(user)

    session = _bridge_session(client)

    assert session["identity"]["provider"] == "google"
    assert session["identity"]["subject"] == "google-123"
    assert session["identity"]["email"] == "google-user@example.com"
    assert session["identity"]["role"] == "viewer"


def test_django_allauth_provider_listing_and_authorize_redirect():
    site = Site.objects.get_current()
    app = SocialApp.objects.create(provider="google", name="Google", client_id="client-id", secret="secret")
    app.sites.add(site)
    client = Client()

    providers = client.get("/api/v1/auth/oauth/providers")
    authorize = client.get("/api/v1/auth/oauth/google/authorize", follow=False)

    assert providers.status_code == 200
    assert {"name": "google", "display_name": "Google"} in _json(providers)["providers"]
    assert authorize.status_code == 302
    assert authorize.headers["Location"] == "/accounts/google/login/"


def test_django_allauth_adapter_rejects_non_allowlisted_social_login(monkeypatch):
    monkeypatch.setattr(
        allauth_adapter,
        "auth_settings",
        SimpleNamespace(allowed_emails=frozenset({"owner@example.com"})),
    )
    monkeypatch.setattr(allauth_adapter, "service", SimpleNamespace(config=SimpleNamespace(mode="production")))
    sociallogin = SimpleNamespace(
        email_addresses=[SimpleNamespace(email="other@example.com")],
        account=SimpleNamespace(extra_data={}),
        user=SimpleNamespace(email="other@example.com"),
    )

    with pytest.raises(ImmediateHttpResponse):
        allauth_adapter.TrackAnywhereSocialAccountAdapter().pre_social_login(None, sociallogin)


def test_django_logout_revokes_track_anywhere_session_and_clears_cookies():
    user = get_user_model().objects.create_user(username="logout-user", email="logout-user@example.com", password="secret")
    ensure_user_role(user, "editor")
    client = Client()
    client.force_login(user)
    _bridge_session(client)

    logout = client.post("/api/v1/auth/logout")
    session = client.get("/api/v1/auth/session")

    assert logout.status_code == 200
    assert _json(logout) == {"authenticated": False}
    assert _json(session) == {"authenticated": False, "identity": None}
