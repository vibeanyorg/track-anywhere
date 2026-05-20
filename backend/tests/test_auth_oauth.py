from __future__ import annotations

import pytest

from track_anywhere.auth_oauth import (
    OAuthProviderSettings,
    auth_settings_from_env,
    identity_from_oauth_token,
    require_allowed_identity,
    role_for_identity,
)
from track_anywhere.errors import PolicyDenied, SecurityPreconditionFailed


OAUTH_ENV_VARS = (
    "TRACK_ANYWHERE_AUTH_SESSION_SECRET",
    "TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_ID",
    "TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_SECRET",
    "TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS",
    "TRACK_ANYWHERE_PASSWORD_SIGNUP_ALLOWED_EMAILS",
    "TRACK_ANYWHERE_OIDC_PROVIDER_NAME",
    "TRACK_ANYWHERE_OIDC_CLIENT_ID",
    "TRACK_ANYWHERE_OIDC_CLIENT_SECRET",
    "TRACK_ANYWHERE_OIDC_METADATA_URL",
    "TRACK_ANYWHERE_OIDC_SCOPE",
)


def clear_oauth_env(monkeypatch) -> None:
    for name in OAUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_google_oauth_settings_are_loaded_from_environment(monkeypatch):
    clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TRACK_ANYWHERE_AUTH_SESSION_SECRET", "secret")
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS", "OWNER@example.com")

    settings = auth_settings_from_env(mode="production")

    assert settings.session_secret == "secret"
    assert settings.allowed_emails == frozenset({"owner@example.com"})
    assert [provider.name for provider in settings.providers] == ["google"]


def test_oauth_provider_requires_allowlist_outside_local_mode(monkeypatch):
    clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TRACK_ANYWHERE_AUTH_SESSION_SECRET", "secret")
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.delenv("TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS", raising=False)

    with pytest.raises(SecurityPreconditionFailed, match="OAUTH_ALLOWED_EMAILS"):
        auth_settings_from_env(mode="production")


def test_oauth_identity_rejects_non_allowlisted_email(monkeypatch):
    clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS", "owner@example.com")
    settings = auth_settings_from_env(mode="local")

    token = {
        "userinfo": {
            "sub": "provider-user-1",
            "email": "other@example.com",
            "email_verified": True,
            "name": "Other User",
        }
    }
    provider = OAuthProviderSettings(
        name="google",
        display_name="Google",
        client_id="id",
        client_secret="secret",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    )
    identity = identity_from_oauth_token(provider, token)

    with pytest.raises(PolicyDenied, match="allowlisted"):
        require_allowed_identity(settings, identity)


def test_oauth_role_selection_uses_explicit_owner_email(monkeypatch):
    clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS", "owner@example.com,viewer@example.com")
    monkeypatch.setenv("TRACK_ANYWHERE_OAUTH_OWNER_EMAILS", "owner@example.com")
    settings = auth_settings_from_env(mode="local")
    provider = OAuthProviderSettings(
        name="google",
        display_name="Google",
        client_id="id",
        client_secret="secret",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    )

    owner = identity_from_oauth_token(
        provider,
        {"userinfo": {"sub": "owner", "email": "owner@example.com", "email_verified": True}},
    )
    viewer = identity_from_oauth_token(
        provider,
        {"userinfo": {"sub": "viewer", "email": "viewer@example.com", "email_verified": True}},
    )

    assert role_for_identity(settings, owner) == "owner"
    assert role_for_identity(settings, viewer) == "viewer"


def test_password_signup_allowlist_is_loaded_from_environment(monkeypatch):
    clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TRACK_ANYWHERE_PASSWORD_SIGNUP_ALLOWED_EMAILS", "FIRST@example.com,second@example.com")

    settings = auth_settings_from_env(mode="local")

    assert settings.password_signup_allowed_emails == frozenset({"first@example.com", "second@example.com"})
