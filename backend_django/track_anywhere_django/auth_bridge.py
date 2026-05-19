from __future__ import annotations

from typing import Any

from track_anywhere.auth_identities import OAuthIdentity
from track_anywhere.security import CredentialReference, hash_secret

from .roles import role_for_user
from .runtime import browser_sessions, service


SESSION_COOKIE = "ta_session"
CSRF_COOKIE = "ta_csrf"
_CREDENTIAL_HASH_KEY = "track_anywhere_credential_hash"
_IDENTITY_KEY = "track_anywhere_identity"


def credential_for_django_user(request) -> CredentialReference | None:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None

    role = role_for_user(user)
    credential_hash = request.session.get(_CREDENTIAL_HASH_KEY)
    identity = request.session.get(_IDENTITY_KEY) or {}
    if credential_hash and identity.get("role") == role:
        credential_ref = CredentialReference(credential_hash)
        try:
            service.actor_from_token(credential_ref)
            return credential_ref
        except Exception:
            request.session.pop(_CREDENTIAL_HASH_KEY, None)
            request.session.pop(_IDENTITY_KEY, None)

    identity = oauth_identity_for_django_user(user)
    login = service.login_oauth_identity(
        identity,
        role=role,
    )
    credential_hash = hash_secret(login["credential_token"])
    request.session[_CREDENTIAL_HASH_KEY] = credential_hash
    request.session[_IDENTITY_KEY] = {**login["identity"], "role": login["membership"]["role"]}
    request.session.modified = True
    return CredentialReference(credential_hash)


def ensure_browser_session_for_django_user(request) -> tuple[str, str] | None:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    pending_session = getattr(request, "_track_anywhere_browser_session", None)
    if pending_session is not None:
        return pending_session
    session_id = request.COOKIES.get(SESSION_COOKIE)
    if credential_for_browser_session(request) is not None:
        return None

    credential = credential_for_django_user(request)
    if credential is None:
        return None
    identity = request.session.get(_IDENTITY_KEY)
    session_id, csrf_token = browser_sessions.issue(
        credential_hash=credential.token_hash,
        identity=dict(identity) if isinstance(identity, dict) else None,
    )
    browser_session = (session_id, csrf_token)
    request._track_anywhere_browser_session = browser_session
    return browser_session


def credential_for_browser_session(request) -> CredentialReference | None:
    session_id = request.COOKIES.get(SESSION_COOKIE)
    credential = browser_sessions.credential_for(session_id)
    if credential is None:
        return None

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        identity = browser_sessions.identity_for(session_id) or {}
        if identity.get("role") != role_for_user(user):
            browser_sessions.revoke(session_id)
            return None
    return credential


def revoke_browser_session_for_request(request) -> None:
    browser_sessions.revoke(request.COOKIES.get(SESSION_COOKIE))
    request.session.pop(_CREDENTIAL_HASH_KEY, None)
    request.session.pop(_IDENTITY_KEY, None)
    request.session.modified = True
    request._track_anywhere_clear_auth_cookies = True


def set_browser_session_cookies(response, *, session_id: str, csrf_token: str, secure: bool) -> None:
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, secure=secure, samesite="Strict")
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, secure=secure, samesite="Strict")


def clear_browser_session_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE, samesite="Strict")
    response.delete_cookie(CSRF_COOKIE, samesite="Strict")


def oauth_identity_for_django_user(user) -> OAuthIdentity:
    social_account = _first_social_account(user)
    if social_account is not None:
        extra_data = social_account.extra_data or {}
        return OAuthIdentity(
            provider=str(social_account.provider),
            subject=str(social_account.uid),
            email=_clean_email(getattr(user, "email", None) or extra_data.get("email")),
            email_verified=_as_bool(extra_data.get("email_verified")),
            name=_clean_optional(user.get_full_name()) or _clean_optional(extra_data.get("name")) or user.get_username(),
            picture=_clean_optional(extra_data.get("picture") or extra_data.get("avatar_url")),
        )
    return OAuthIdentity(
        provider="django",
        subject=str(user.pk),
        email=_clean_email(getattr(user, "email", None)),
        email_verified=True,
        name=_clean_optional(user.get_full_name()) or user.get_username(),
        picture=None,
    )


def configured_allauth_providers() -> list[dict[str, str]]:
    try:
        from allauth.socialaccount.models import SocialApp
        from django.conf import settings
    except Exception:
        return []

    try:
        apps = SocialApp.objects.filter(sites__id=settings.SITE_ID).order_by("provider", "name")
        return [{"name": app.provider, "display_name": app.name or app.provider.title()} for app in apps]
    except Exception:
        return []


def _first_social_account(user):
    try:
        from allauth.socialaccount.models import SocialAccount
    except Exception:
        return None
    return SocialAccount.objects.filter(user=user).order_by("provider", "uid").first()


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_email(value: Any) -> str | None:
    text = _clean_optional(value)
    return text.lower() if text else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return False
