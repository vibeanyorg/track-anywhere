from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import Request

from .auth_identities import OAuthIdentity
from .errors import PolicyDenied, SecurityPreconditionFailed
from .service_auth import ROLE_SCOPES


_PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_LOCAL_SESSION_SECRET = "track-anywhere-local-dev-session-secret"
_GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


@dataclass(frozen=True)
class OAuthProviderSettings:
    name: str
    display_name: str
    client_id: str
    client_secret: str
    server_metadata_url: str
    scope: str = "openid profile email"

    def public_dict(self) -> dict[str, str]:
        return {"name": self.name, "display_name": self.display_name, "scope": self.scope}


@dataclass(frozen=True)
class AuthSettings:
    session_secret: str | None
    public_base_url: str | None
    success_redirect_url: str | None
    allowed_emails: frozenset[str]
    owner_emails: frozenset[str]
    password_signup_allowed_emails: frozenset[str]
    default_role: str
    providers: tuple[OAuthProviderSettings, ...]

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.providers)

    def provider(self, name: str) -> OAuthProviderSettings | None:
        return next((provider for provider in self.providers if provider.name == name), None)


def auth_settings_from_env(*, mode: str) -> AuthSettings:
    session_secret = _clean_optional(os.getenv("TRACK_ANYWHERE_AUTH_SESSION_SECRET"))
    public_base_url = _clean_optional(os.getenv("TRACK_ANYWHERE_PUBLIC_BASE_URL"))
    success_redirect_url = _clean_optional(os.getenv("TRACK_ANYWHERE_AUTH_SUCCESS_REDIRECT"))
    allowed_emails = frozenset(
        item.lower()
        for item in _split_csv(os.getenv("TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS"))
    )
    owner_emails = frozenset(
        item.lower()
        for item in _split_csv(os.getenv("TRACK_ANYWHERE_OAUTH_OWNER_EMAILS"))
    )
    password_signup_allowed_emails = frozenset(
        item.lower()
        for item in _split_csv(os.getenv("TRACK_ANYWHERE_PASSWORD_SIGNUP_ALLOWED_EMAILS"))
    )
    default_role = _clean_optional(os.getenv("TRACK_ANYWHERE_OAUTH_DEFAULT_ROLE")) or "viewer"
    _validate_role(default_role)
    providers = tuple(_configured_providers())

    if providers and mode != "local":
        if not session_secret:
            raise SecurityPreconditionFailed(
                "OAuth login requires TRACK_ANYWHERE_AUTH_SESSION_SECRET outside local mode"
            )
        if not allowed_emails:
            raise SecurityPreconditionFailed(
                "OAuth login requires TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS until multi-user account mapping exists"
            )

    if session_secret is None and mode == "local":
        session_secret = _LOCAL_SESSION_SECRET

    return AuthSettings(
        session_secret=session_secret,
        public_base_url=public_base_url.rstrip("/") if public_base_url else None,
        success_redirect_url=success_redirect_url,
        allowed_emails=allowed_emails,
        owner_emails=owner_emails,
        password_signup_allowed_emails=password_signup_allowed_emails,
        default_role=default_role,
        providers=providers,
    )


def build_oauth_registry(settings: AuthSettings) -> OAuth:
    oauth = OAuth()
    for provider in settings.providers:
        oauth.register(
            provider.name,
            client_id=provider.client_id,
            client_secret=provider.client_secret,
            server_metadata_url=provider.server_metadata_url,
            client_kwargs={"scope": provider.scope, "code_challenge_method": "S256"},
        )
    return oauth


def oauth_callback_url(request: Request, settings: AuthSettings, provider: str) -> str:
    if settings.public_base_url:
        return f"{settings.public_base_url}/api/v1/auth/oauth/{provider}/callback"
    return str(request.url_for("oauth_callback", provider=provider))


def identity_from_oauth_token(provider: OAuthProviderSettings, token: dict[str, Any]) -> OAuthIdentity:
    userinfo = token.get("userinfo")
    if not isinstance(userinfo, dict):
        raise PolicyDenied("OAuth provider did not return OIDC user info")

    subject = _clean_optional(userinfo.get("sub"))
    if subject is None:
        raise PolicyDenied("OAuth provider did not return a stable subject")

    email = _clean_optional(userinfo.get("email"))
    return OAuthIdentity(
        provider=provider.name,
        subject=subject,
        email=email.lower() if email else None,
        email_verified=_as_bool(userinfo.get("email_verified")),
        name=_clean_optional(userinfo.get("name")),
        picture=_clean_optional(userinfo.get("picture")),
    )


def require_allowed_identity(settings: AuthSettings, identity: OAuthIdentity) -> None:
    if identity.email is None:
        raise PolicyDenied("OAuth identity did not include an email address")
    if not identity.email_verified:
        raise PolicyDenied("OAuth identity email must be verified")
    if settings.allowed_emails and identity.email.lower() not in settings.allowed_emails:
        raise PolicyDenied("OAuth identity email is not allowlisted")


def role_for_identity(settings: AuthSettings, identity: OAuthIdentity) -> str:
    if identity.email and identity.email.lower() in settings.owner_emails:
        return "owner"
    return settings.default_role


def _configured_providers() -> list[OAuthProviderSettings]:
    providers: list[OAuthProviderSettings] = []

    google_client_id = _clean_optional(os.getenv("TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_ID"))
    google_client_secret = _clean_optional(os.getenv("TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_SECRET"))
    _validate_pair("Google OAuth", google_client_id, google_client_secret)
    if google_client_id and google_client_secret:
        providers.append(
            OAuthProviderSettings(
                name="google",
                display_name="Google",
                client_id=google_client_id,
                client_secret=google_client_secret,
                server_metadata_url=_GOOGLE_METADATA_URL,
            )
        )

    oidc_name = _clean_optional(os.getenv("TRACK_ANYWHERE_OIDC_PROVIDER_NAME")) or "oidc"
    oidc_client_id = _clean_optional(os.getenv("TRACK_ANYWHERE_OIDC_CLIENT_ID"))
    oidc_client_secret = _clean_optional(os.getenv("TRACK_ANYWHERE_OIDC_CLIENT_SECRET"))
    oidc_metadata_url = _clean_optional(os.getenv("TRACK_ANYWHERE_OIDC_METADATA_URL"))
    oidc_scope = _clean_optional(os.getenv("TRACK_ANYWHERE_OIDC_SCOPE")) or "openid profile email"
    _validate_provider_name(oidc_name)
    _validate_triplet("OIDC provider", oidc_client_id, oidc_client_secret, oidc_metadata_url)
    if oidc_client_id and oidc_client_secret and oidc_metadata_url:
        providers.append(
            OAuthProviderSettings(
                name=oidc_name,
                display_name=oidc_name.upper(),
                client_id=oidc_client_id,
                client_secret=oidc_client_secret,
                server_metadata_url=oidc_metadata_url,
                scope=oidc_scope,
            )
        )

    return providers


def _validate_pair(label: str, left: str | None, right: str | None) -> None:
    if bool(left) != bool(right):
        raise SecurityPreconditionFailed(f"{label} requires both client id and client secret")


def _validate_triplet(label: str, first: str | None, second: str | None, third: str | None) -> None:
    values = [first, second, third]
    if any(values) and not all(values):
        raise SecurityPreconditionFailed(f"{label} requires client id, client secret, and metadata URL")


def _validate_provider_name(value: str) -> None:
    if not _PROVIDER_NAME_RE.match(value):
        raise SecurityPreconditionFailed("OIDC provider name must be a lowercase slug")


def _validate_role(value: str) -> None:
    if value not in ROLE_SCOPES:
        raise SecurityPreconditionFailed(f"OAuth default role must be one of {sorted(ROLE_SCOPES)}")


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return False
