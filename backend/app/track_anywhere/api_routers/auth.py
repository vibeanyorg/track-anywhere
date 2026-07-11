from __future__ import annotations

from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..api_auth_runtime import auth_cookie_secure, auth_settings, oauth_registry
from ..api_browser_sessions import browser_sessions
from ..api_dependencies import AuthToken
from ..api_ports.auth import AuthService
from ..api_sessions import SESSION_COOKIE, clear_browser_session_cookies, set_browser_session_cookies
from ..auth_oauth import identity_from_oauth_token, oauth_callback_url, require_allowed_identity, role_for_identity
from ..errors import PolicyDenied, RateLimitExceeded, ValidationError
from ..password_auth import PasswordAccount, PasswordLoginCommand, PasswordSignupCommand
from ..platform_auth import ApiKeySessionCommand
from ..platform_auth_http import identity_for_actor


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/session")
def current_session(request: Request, auth_service: AuthService):
    session_id = request.cookies.get(SESSION_COOKIE)
    identity = browser_sessions.identity_for(session_id)
    credential = browser_sessions.credential_for(session_id)
    if identity is None or credential is None:
        return {"authenticated": False, "identity": None}
    try:
        auth_service.actor_from_token(credential)
    except PolicyDenied:
        return {"authenticated": False, "identity": None}
    return {"authenticated": True, "identity": identity}


@router.get("/token-status", dependencies=[])
def token_status(token: AuthToken, auth_service: AuthService):
    try:
        return auth_service.credential_status(token)
    except PolicyDenied as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/logout")
def logout(request: Request):
    browser_sessions.revoke(request.cookies.get(SESSION_COOKIE))
    if _accepts_html(request):
        response = RedirectResponse("/api/v1/auth/login", status_code=303)
    else:
        response = JSONResponse({"authenticated": False})
    clear_browser_session_cookies(response)
    return response


@router.post("/session/api-key")
def create_api_key_session(payload: ApiKeySessionCommand, auth_service: AuthService):
    try:
        actor = auth_service.actor_from_token(payload.api_key)
    except PolicyDenied as exc:
        auth_service.record_security_failure("auth.api_key_denied", {"reason": str(exc)})
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
        secure=auth_cookie_secure(),
    )
    return response


@router.post("/password/signup")
def signup_with_password(payload: PasswordSignupCommand, auth_service: AuthService):
    try:
        account = auth_service.create_password_account(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            signup_allowed_emails=auth_settings.password_signup_allowed_emails,
        )
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail="password signup is not allowlisted") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _password_session_response(auth_service, account)


@router.post("/password/login")
def login_with_password(payload: PasswordLoginCommand, request: Request, auth_service: AuthService):
    try:
        account = auth_service.authenticate_password_account(
            email=payload.email,
            password=payload.password,
            source=request.client.host if request.client else "unknown",
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail="too many password login attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except PolicyDenied as exc:
        raise HTTPException(status_code=401, detail="email or password is incorrect") from exc
    return _password_session_response(auth_service, account)


@router.get("/oauth/providers")
def list_oauth_providers():
    return {"providers": [provider.public_dict() for provider in auth_settings.providers]}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(provider: str, request: Request, next: str | None = None):
    provider_settings = auth_settings.provider(provider)
    if provider_settings is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not configured")

    client = oauth_registry.create_client(provider)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth provider is unavailable")

    redirect_uri = oauth_callback_url(request, auth_settings, provider)
    if next and hasattr(request, "session"):
        request.session["ta_auth_next"] = _safe_next(next)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(provider: str, request: Request, auth_service: AuthService):
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
        login = auth_service.login_oauth_identity(identity, role=role_for_identity(auth_settings, identity))
    except OAuthError as exc:
        auth_service.record_security_failure("auth.oauth_callback_failed", {"provider": provider, "error": exc.error})
        raise HTTPException(status_code=400, detail="OAuth callback rejected") from exc
    except PolicyDenied as exc:
        auth_service.record_security_failure("auth.oauth_denied", {"provider": provider, "reason": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    session_identity = {**identity.public_dict(), "user_id": login["user"]["user_id"], "role": login["membership"]["role"]}
    session_id, csrf_token = browser_sessions.issue(
        credential_token=login["credential_token"],
        identity=session_identity,
    )
    response = _success_response(request, csrf_token=csrf_token, identity=session_identity)
    set_browser_session_cookies(
        response,
        session_id=session_id,
        csrf_token=csrf_token,
        secure=auth_cookie_secure(),
    )
    return response


def _success_response(request: Request, *, csrf_token: str, identity: dict):
    redirect_to = auth_settings.success_redirect_url
    if not redirect_to and hasattr(request, "session"):
        redirect_to = request.session.pop("ta_auth_next", None)
    if redirect_to:
        return RedirectResponse(redirect_to, status_code=303)
    return JSONResponse({"authenticated": True, "csrf_token": csrf_token, "identity": identity})


def _safe_next(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/api/v1/auth/session-view"


def _accepts_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def _password_session_response(auth_service: AuthService, account: PasswordAccount) -> JSONResponse:
    login = auth_service.login_password_account(account)
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
        secure=auth_cookie_secure(),
    )
    return response
