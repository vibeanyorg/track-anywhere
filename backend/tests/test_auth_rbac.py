from __future__ import annotations

import pytest

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.errors import PolicyDenied
from track_anywhere.service import FinanceService


def oauth_identity(email: str, subject: str = "subject-1") -> OAuthIdentity:
    return OAuthIdentity(
        provider="google",
        subject=subject,
        email=email,
        email_verified=True,
        name=email.split("@", 1)[0],
        picture=None,
    )


def test_oauth_login_creates_persistent_user_identity_and_book_role(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'auth-rbac.sqlite3'}"
    service = FinanceService(database_url=database_url)

    login = service.login_oauth_identity(oauth_identity("viewer@example.com"), role="viewer")

    assert login["identity"]["provider"] == "google"
    assert login["identity"]["email"] == "viewer@example.com"
    assert login["membership"]["role"] == "viewer"
    actor = service.actor_from_token(login["credential_token"], "account:read")
    assert actor.actor_id == login["user"]["user_id"]

    with pytest.raises(PolicyDenied):
        service.create_account(
            login["credential_token"],
            {"name": "Viewer Cash", "type": "asset", "currency": "CNY"},
            idempotency_key="viewer-cash-denied",
        )

    reloaded = FinanceService(database_url=database_url)
    persisted = reloaded.auth_identities.get_by_provider_subject("google", "subject-1")
    assert persisted is not None
    assert persisted.user_id == login["user"]["user_id"]
    assert reloaded.books.members[("book_default", persisted.user_id)].role == "viewer"


def test_oauth_login_uses_incremental_persist(monkeypatch, tmp_path):
    database_url = f"sqlite:///{tmp_path / 'auth-rbac-incremental.sqlite3'}"
    service = FinanceService(database_url=database_url)
    saved_states = []

    def fail_full_save(_service):
        raise AssertionError("login should not full-save service state")

    def capture_login_state(**kwargs):
        saved_states.append(kwargs)

    monkeypatch.setattr(service.storage, "save_full_snapshot_for_legacy_bootstrap", fail_full_save)
    monkeypatch.setattr(service.storage, "save_auth_login_state", capture_login_state)

    login = service.login_oauth_identity(oauth_identity("incremental@example.com"), role="viewer")

    assert len(saved_states) == 1
    persisted = saved_states[0]
    assert persisted["book"].book_id == "book_default"
    assert persisted["user"].user_id == login["user"]["user_id"]
    assert persisted["identity"].identity_id == login["identity"]["identity_id"]
    assert persisted["credential"].actor.actor_id == login["user"]["user_id"]
    assert persisted["audit_event"].operation == "auth.login"
    assert {member.user_id for member in persisted["members"]} == {"owner", login["user"]["user_id"]}


def test_oauth_login_reuses_identity_and_can_promote_role(tmp_path):
    service = FinanceService(database_url=f"sqlite:///{tmp_path / 'auth-rbac-promote.sqlite3'}")
    identity = oauth_identity("owner@example.com", subject="subject-owner")

    first = service.login_oauth_identity(identity, role="viewer")
    second = service.login_oauth_identity(identity, role="owner")

    assert second["user"]["user_id"] == first["user"]["user_id"]
    assert second["identity"]["identity_id"] == first["identity"]["identity_id"]
    assert second["membership"]["role"] == "owner"
    account, replay = service.create_account(
        second["credential_token"],
        {"name": "Owner Cash", "type": "asset", "currency": "CNY"},
        idempotency_key="owner-cash-create",
    )
    assert replay is False
    assert account.name == "Owner Cash"
