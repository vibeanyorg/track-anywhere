from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ...auth.contracts import (
    ApiKeySessionCommand,
    DeviceApprovalCommand,
    OAuthAuthorizationCodeTokenCommand,
    OAuthAuthorizeCommand,
    OAuthDeviceAuthorizeCommand,
    OAuthDeviceTokenCommand,
    OAuthRegisterCommand,
    OAuthRevokeCommand,
)
from ...auth.device import PersistentDeviceService
from ...auth.errors import AuthPolicyDenied, AuthSecurityError, OAuthFlowError
from ...auth.http import (
    SESSION_COOKIE,
    clear_browser_session_cookies,
    set_browser_session_cookies,
)
from ...auth.security import (
    authorization_server_metadata,
    protected_resource_metadata,
    require_same_origin,
)
from ...auth.oauth import PersistentOAuthService
from ...auth.sessions import ActiveBrowserSession, PersistentSessionService


SessionDependency = Callable[[], Iterator[Session]]


def create_auth_router(
    get_session: SessionDependency,
    *,
    cookie_secure: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    @router.get("/oauth/authorization-server", tags=["oauth"])
    def oauth_authorization_server(request: Request) -> dict[str, object]:
        return authorization_server_metadata(_issuer_for(request))

    @router.get("/oauth/protected-resource", tags=["oauth"])
    def oauth_protected_resource(request: Request) -> dict[str, object]:
        return protected_resource_metadata(_issuer_for(request))

    @router.post("/oauth/register", tags=["oauth"])
    def register_oauth_client(
        payload: OAuthRegisterCommand,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            body = PersistentOAuthService(session).register_client(payload)
        except AuthSecurityError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/oauth/authorize", tags=["oauth"])
    def authorize_oauth_client(
        payload: OAuthAuthorizeCommand,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, str]:
        session_service = PersistentSessionService(session)
        active = _require_browser_request(request, session_service)
        try:
            return PersistentOAuthService(session).authorize(payload, active)
        except AuthPolicyDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except AuthSecurityError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/oauth/device/authorize", tags=["oauth"])
    def create_device_authorization(
        payload: OAuthDeviceAuthorizeCommand,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            body = PersistentDeviceService(session).create_authorization(
                payload, _issuer_for(request)
            )
        except AuthPolicyDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except AuthSecurityError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/oauth/token", tags=["oauth"])
    def exchange_oauth_token(
        payload: OAuthAuthorizationCodeTokenCommand | OAuthDeviceTokenCommand,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            if isinstance(payload, OAuthDeviceTokenCommand):
                body = PersistentDeviceService(session).exchange(payload)
            else:
                body = PersistentOAuthService(session).exchange_code(payload)
        except OAuthFlowError as error:
            response = JSONResponse(
                {"error": error.error, "error_description": error.description, **error.extra},
                status_code=400,
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @router.post("/oauth/revoke", tags=["oauth"])
    def revoke_oauth_token(
        payload: OAuthRevokeCommand,
        session: Session = Depends(get_session),
    ) -> dict[str, bool]:
        return PersistentOAuthService(session).revoke(payload.token)

    @router.post("/auth/session/api-key", tags=["auth"])
    def create_api_key_session(
        payload: ApiKeySessionCommand,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            issued = PersistentSessionService(session).issue_from_api_key(payload.api_key)
        except AuthPolicyDenied as error:
            raise HTTPException(
                status_code=401,
                detail="API key is invalid or expired",
            ) from error
        response = JSONResponse(
            {
                "authenticated": True,
                "csrf_token": issued.csrf_token,
                "identity": issued.identity.public_dict(),
            }
        )
        set_browser_session_cookies(
            response,
            session_token=issued.session_token,
            csrf_token=issued.csrf_token,
            secure=cookie_secure,
        )
        return response

    @router.get("/auth/session", tags=["auth"])
    def current_session(
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        active = PersistentSessionService(session).current(
            request.cookies.get(SESSION_COOKIE)
        )
        if active is None:
            return {"authenticated": False, "identity": None}
        return {"authenticated": True, "identity": active.identity.public_dict()}

    @router.post("/auth/device", tags=["auth"])
    def approve_device_authorization(
        payload: DeviceApprovalCommand,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        active = _require_browser_request(
            request, PersistentSessionService(session)
        )
        try:
            return PersistentDeviceService(session).approve(payload, active)
        except AuthPolicyDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except AuthSecurityError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/auth/token-status", tags=["auth"])
    def token_status(
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        raw_token = _bearer_token(request)
        try:
            return PersistentOAuthService(session).token_status(raw_token)
        except AuthPolicyDenied as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    @router.post("/auth/logout", tags=["auth"])
    def logout(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        service = PersistentSessionService(session)
        active = _require_browser_request(request, service)
        service.revoke(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"authenticated": False})
        clear_browser_session_cookies(response)
        _ = active
        return response

    return router


def _issuer_for(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer credential is required")
    return token


def _require_browser_request(
    request: Request,
    service: PersistentSessionService,
) -> ActiveBrowserSession:
    active = service.current(request.cookies.get(SESSION_COOKIE), lock=True)
    csrf_token = request.headers.get("X-CSRF-Token")
    if active is None or not service.verify_csrf(active, csrf_token):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
    try:
        require_same_origin(
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            allowed_origin=_issuer_for(request),
        )
    except AuthSecurityError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return active


__all__ = ["SessionDependency", "create_auth_router"]
