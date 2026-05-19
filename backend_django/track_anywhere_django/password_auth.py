from __future__ import annotations

import re

from django.contrib.auth import authenticate, get_user_model, login as django_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from ninja.errors import HttpError

from track_anywhere.password_auth import PasswordLoginCommand, PasswordSignupCommand, normalize_email

from .auth_bridge import SESSION_COOKIE, credential_for_django_user, set_browser_session_cookies
from .roles import ensure_user_role
from .runtime import browser_sessions, service


def login_password_session(request: HttpRequest, payload: PasswordLoginCommand) -> JsonResponse:
    user = _user_for_email(payload.email)
    if user is None:
        service.record_security_failure("auth.password_denied", {"reason": "bad_credentials"})
        raise HttpError(401, "email or password is incorrect")
    authenticated = authenticate(request, username=user.get_username(), password=payload.password)
    if authenticated is None:
        service.record_security_failure("auth.password_denied", {"reason": "bad_credentials"})
        raise HttpError(401, "email or password is incorrect")
    django_login(request, authenticated, backend="django.contrib.auth.backends.ModelBackend")
    return _issue_session_response(request)


def signup_password_session(request: HttpRequest, payload: PasswordSignupCommand) -> JsonResponse:
    UserModel = get_user_model()
    email = normalize_email(payload.email)
    if UserModel.objects.filter(email__iexact=email).exists():
        raise HttpError(409, "email is already registered")
    try:
        validate_password(payload.password)
    except DjangoValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc

    with transaction.atomic():
        first_user = not UserModel.objects.exists()
        user = UserModel.objects.create_user(
            username=_available_username(email),
            email=email,
            password=payload.password,
            first_name=(payload.display_name or "")[:150],
        )
        ensure_user_role(user, "owner" if first_user else "viewer")

    authenticated = authenticate(request, username=user.get_username(), password=payload.password)
    if authenticated is None:
        raise HttpError(500, "created user could not be authenticated")
    django_login(request, authenticated, backend="django.contrib.auth.backends.ModelBackend")
    return _issue_session_response(request)


def _issue_session_response(request: HttpRequest) -> JsonResponse:
    browser_sessions.revoke(request.COOKIES.get(SESSION_COOKIE))
    credential = credential_for_django_user(request)
    if credential is None:
        raise HttpError(500, "failed to issue Track Anywhere credential")
    identity = request.session.get("track_anywhere_identity") or {}
    session_id, csrf_token = browser_sessions.issue(
        credential_hash=credential.token_hash,
        identity=dict(identity) if isinstance(identity, dict) else None,
    )
    request._track_anywhere_browser_session = (session_id, csrf_token)
    response = JsonResponse({"authenticated": True, "csrf_token": csrf_token, "identity": identity})
    set_browser_session_cookies(
        response,
        session_id=session_id,
        csrf_token=csrf_token,
        secure=service.config.mode != "local",
    )
    return response


def _user_for_email(email: str):
    UserModel = get_user_model()
    return UserModel.objects.filter(email__iexact=normalize_email(email)).order_by("pk").first()


def _available_username(email: str) -> str:
    UserModel = get_user_model()
    local_part = email.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9_]+", "_", local_part).strip("_") or "user"
    base = base[:120]
    username = base
    suffix = 2
    while UserModel.objects.filter(username=username).exists():
        username = f"{base}_{suffix}"[:150]
        suffix += 1
    return username
