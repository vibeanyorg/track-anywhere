from __future__ import annotations

from typing import Any

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.http import HttpResponseForbidden

from .runtime import auth_settings, service


class TrackAnywhereSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin) -> None:
        email = _social_login_email(sociallogin)
        if email is None:
            raise ImmediateHttpResponse(HttpResponseForbidden("OAuth identity did not include an email address"))
        if service.config.mode != "local" and not auth_settings.allowed_emails:
            raise ImmediateHttpResponse(HttpResponseForbidden("OAuth login requires an email allowlist"))
        if auth_settings.allowed_emails and email.lower() not in auth_settings.allowed_emails:
            raise ImmediateHttpResponse(HttpResponseForbidden("OAuth identity email is not allowlisted"))


def _social_login_email(sociallogin: Any) -> str | None:
    for email_address in getattr(sociallogin, "email_addresses", []) or []:
        email = getattr(email_address, "email", None)
        if email:
            return str(email).strip().lower()

    account = getattr(sociallogin, "account", None)
    extra_data = getattr(account, "extra_data", None) or {}
    email = extra_data.get("email")
    if email:
        return str(email).strip().lower()

    user = getattr(sociallogin, "user", None)
    email = getattr(user, "email", None)
    return str(email).strip().lower() if email else None
