from __future__ import annotations

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..api_sessions import SESSION_COOKIE, clear_browser_session_cookies, set_browser_session_cookies
from ..api_runtime import auth_settings, browser_sessions, oauth_registry, password_accounts, service
from ..auth_identities import OAuthIdentity
from ..auth_oauth import identity_from_oauth_token, oauth_callback_url, require_allowed_identity, role_for_identity
from ..errors import PolicyDenied, ValidationError
from ..password_auth import PasswordLoginCommand, PasswordSignupCommand
from ..platform_auth import ApiKeySessionCommand
from ..platform_auth_http import identity_for_actor


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/session")
def current_session(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE)
    identity = browser_sessions.identity_for(session_id)
    credential = browser_sessions.credential_for(session_id)
    if identity is None or credential is None:
        return {"authenticated": False, "identity": None}
    try:
        service.actor_from_token(credential)
    except PolicyDenied:
        return {"authenticated": False, "identity": None}
    return {"authenticated": True, "identity": identity}


@router.post("/logout")
def logout(request: Request):
    browser_sessions.revoke(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"authenticated": False})
    clear_browser_session_cookies(response)
    return response


@router.post("/session/api-key")
def create_api_key_session(payload: ApiKeySessionCommand):
    try:
        actor = service.actor_from_token(payload.api_key)
    except PolicyDenied as exc:
        service.record_security_failure("auth.api_key_denied", {"reason": str(exc)})
        raise HTTPException(status_code=401, detail="API key is invalid or expired") from exc

    session_identity = identity_for_actor(actor, provider="api-key")
    session_id, csrf_token = browser_sessions.issue(
        credential_token=payload.api_key,
        identity=session_identity,
    )
    response = JSONResponse({"authenticated": True, "csrf_token": csrf_token, "identity": session_identity})
    set_browser_session_cookies(
        response,
        session_id=session_id,
        csrf_token=csrf_token,
        secure=service.config.mode != "local",
    )
    return response


@router.post("/password/signup")
def signup_with_password(payload: PasswordSignupCommand):
    if service.config.mode != "local" and payload.email not in auth_settings.password_signup_allowed_emails:
        service.record_security_failure("auth.password_signup_denied", {"reason": "email_not_allowlisted"})
        raise HTTPException(status_code=403, detail="password signup is not allowlisted")
    try:
        account = password_accounts.create(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _password_session_response(account.email, account.display_name, account.role)


@router.post("/password/login")
def login_with_password(payload: PasswordLoginCommand):
    try:
        account = password_accounts.authenticate(email=payload.email, password=payload.password)
    except PolicyDenied as exc:
        service.record_security_failure("auth.password_denied", {"reason": "bad_credentials"})
        raise HTTPException(status_code=401, detail="email or password is incorrect") from exc
    return _password_session_response(account.email, account.display_name, account.role)


@router.get("/oauth/providers")
def list_oauth_providers():
    return {"providers": [provider.public_dict() for provider in auth_settings.providers]}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(provider: str, request: Request):
    provider_settings = auth_settings.provider(provider)
    if provider_settings is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not configured")

    client = oauth_registry.create_client(provider)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth provider is unavailable")

    redirect_uri = oauth_callback_url(request, auth_settings, provider)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(provider: str, request: Request):
    provider_settings = auth_settings.provider(provider)
    if provider_settings is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not configured")

    client = oauth_registry.create_client(provider)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth provider is unavailable")

    try:
        token = await client.authorize_access_token(request)
        identity = identity_from_oauth_token(provider_settings, token)
        require_allowed_identity(auth_settings, identity)
        login = service.login_oauth_identity(identity, role=role_for_identity(auth_settings, identity))
    except OAuthError as exc:
        service.record_security_failure("auth.oauth_callback_failed", {"provider": provider, "error": exc.error})
        raise HTTPException(status_code=400, detail="OAuth callback rejected") from exc
    except PolicyDenied as exc:
        service.record_security_failure("auth.oauth_denied", {"provider": provider, "reason": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    session_identity = {**identity.public_dict(), "user_id": login["user"]["user_id"], "role": login["membership"]["role"]}
    session_id, csrf_token = browser_sessions.issue(
        credential_token=login["credential_token"],
        identity=session_identity,
    )
    response = _success_response(csrf_token=csrf_token, identity=session_identity)
    set_browser_session_cookies(
        response,
        session_id=session_id,
        csrf_token=csrf_token,
        secure=service.config.mode != "local",
    )
    return response


def _success_response(*, csrf_token: str, identity: dict):
    if auth_settings.success_redirect_url:
        return RedirectResponse(auth_settings.success_redirect_url, status_code=303)
    return JSONResponse({"authenticated": True, "csrf_token": csrf_token, "identity": identity})


def _password_session_response(email: str, display_name: str, role: str):
    login = service.login_oauth_identity(
        OAuthIdentity(
            provider="password",
            subject=email,
            email=email,
            email_verified=True,
            name=display_name,
            picture=None,
        ),
        role=role,
    )
    session_identity = {**login["identity"], "role": login["membership"]["role"]}
    session_id, csrf_token = browser_sessions.issue(
        credential_token=login["credential_token"],
        identity=session_identity,
    )
    response = JSONResponse({"authenticated": True, "csrf_token": csrf_token, "identity": session_identity})
    set_browser_session_cookies(
        response,
        session_id=session_id,
        csrf_token=csrf_token,
        secure=service.config.mode != "local",
    )
    return response
