from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TypeVar
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from ...auth.contracts import (
    ApiKeySessionCommand,
    DeviceApprovalCommand,
    OAuthAuthorizationCodeTokenCommand,
    OAuthAuthorizeCommand,
    OAuthDeviceAuthorizeCommand,
    OAuthDeviceTokenCommand,
    OAuthRefreshTokenCommand,
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
from ...auth.oauth import OAUTH_ACCESS_KINDS, PersistentOAuthService
from ...auth.resources import api_resource, canonical_public_base_url
from ...auth.security import (
    authorization_server_metadata,
    protected_resource_metadata,
    protected_resource_metadata_url,
    require_same_origin,
)
from ...auth.sessions import ActiveBrowserSession, PersistentSessionService


SessionDependency = Callable[[], Iterator[Session]]
ProtocolCommand = TypeVar("ProtocolCommand", bound=BaseModel)
_TOKEN_ADAPTER = TypeAdapter(
    OAuthAuthorizationCodeTokenCommand
    | OAuthDeviceTokenCommand
    | OAuthRefreshTokenCommand
)
_INVALID_REQUEST_DESCRIPTION = "OAuth request parameters are invalid"


class _OAuthRequestParsingError(AuthSecurityError):
    """Malformed protocol input that must not be reflected back to the caller."""


def create_auth_router(
    get_session: SessionDependency,
    *,
    cookie_secure: bool = False,
    public_base_url: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    @router.get("/oauth/authorization-server", tags=["oauth"])
    def oauth_authorization_server(request: Request) -> dict[str, object]:
        return authorization_server_metadata(_public_base_for(request, public_base_url))

    @router.get("/oauth/protected-resource", tags=["oauth"])
    def oauth_protected_resource(request: Request) -> dict[str, object]:
        base = _public_base_for(request, public_base_url)
        return protected_resource_metadata(base, api_resource(base))

    @router.post("/oauth/register", tags=["oauth"])
    async def register_oauth_client(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            payload = await _command_from_request(request, OAuthRegisterCommand)
            body = PersistentOAuthService(
                session,
                _public_base_for(request, public_base_url),
            ).register_client(payload)
        except (AuthSecurityError, ValidationError):
            return _oauth_error(
                "invalid_client_metadata",
                "OAuth client metadata is invalid",
            )
        response = JSONResponse(body, status_code=201)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/oauth/authorize", tags=["oauth"], response_model=None)
    def begin_oauth_authorization(
        request: Request,
        session: Session = Depends(get_session),
    ) -> RedirectResponse | JSONResponse:
        try:
            payload = _command_from_query(request, OAuthAuthorizeCommand)
            PersistentOAuthService(
                session,
                _public_base_for(request, public_base_url),
            ).validate_authorization_request(payload)
        except (AuthPolicyDenied, AuthSecurityError, ValidationError):
            return _oauth_error("invalid_request", _INVALID_REQUEST_DESCRIPTION)
        query = urlencode(
            payload.model_dump(exclude={"action"}, exclude_none=True),
            doseq=True,
        )
        return RedirectResponse(f"/auth/callback?{query}", status_code=302)

    @router.post("/oauth/authorize", tags=["oauth"], response_model=None)
    async def authorize_oauth_client(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse | RedirectResponse:
        try:
            payload = await _command_from_request(request, OAuthAuthorizeCommand)
        except (AuthSecurityError, ValidationError):
            return _oauth_error("invalid_request", _INVALID_REQUEST_DESCRIPTION)
        session_service = PersistentSessionService(session)
        active = _require_browser_request(
            request,
            session_service,
            public_base_url,
        )
        try:
            body = PersistentOAuthService(
                session,
                _public_base_for(request, public_base_url),
            ).authorize(payload, active)
        except AuthPolicyDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except AuthSecurityError as error:
            return _oauth_error("invalid_request", str(error))
        if _is_form_request(request):
            return RedirectResponse(body["redirect_uri"], status_code=303)
        return JSONResponse(body)

    @router.post("/oauth/device/authorize", tags=["oauth"])
    async def create_device_authorization(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            payload = await _command_from_request(
                request,
                OAuthDeviceAuthorizeCommand,
            )
            base = _public_base_for(request, public_base_url)
            body = PersistentDeviceService(session, base).create_authorization(
                payload,
                base,
            )
        except (AuthPolicyDenied, AuthSecurityError, ValidationError):
            return _oauth_error("invalid_request", _INVALID_REQUEST_DESCRIPTION)
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/oauth/token", tags=["oauth"])
    async def exchange_oauth_token(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            raw_payload = await _protocol_payload(request)
            payload = _TOKEN_ADAPTER.validate_python(raw_payload)
            base = _public_base_for(request, public_base_url)
            if isinstance(payload, OAuthDeviceTokenCommand):
                body = PersistentDeviceService(session, base).exchange(payload)
            elif isinstance(payload, OAuthRefreshTokenCommand):
                body = PersistentOAuthService(session, base).exchange_refresh(payload)
            else:
                body = PersistentOAuthService(session, base).exchange_code(payload)
        except (_OAuthRequestParsingError, ValidationError):
            return _oauth_error("invalid_request", _INVALID_REQUEST_DESCRIPTION)
        except AuthSecurityError:
            return _oauth_error(
                "invalid_target",
                "The requested resource is not served by this authorization server",
            )
        except OAuthFlowError as error:
            return _oauth_error(error.error, error.description, error.extra)
        response = JSONResponse(body)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @router.post("/oauth/revoke", tags=["oauth"])
    async def revoke_oauth_token(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            payload = await _command_from_request(request, OAuthRevokeCommand)
        except (AuthSecurityError, ValidationError):
            return _oauth_error("invalid_request", _INVALID_REQUEST_DESCRIPTION)
        body = PersistentOAuthService(
            session,
            _public_base_for(request, public_base_url),
        ).revoke(payload.token, client_id=payload.client_id)
        return JSONResponse(body)

    @router.post("/auth/session/api-key", tags=["auth"])
    def create_api_key_session(
        payload: ApiKeySessionCommand,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            issued = PersistentSessionService(session).issue_from_api_key(
                payload.api_key
            )
        except AuthPolicyDenied as error:
            raise HTTPException(
                status_code=401, detail="API key is invalid or expired"
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
            request,
            PersistentSessionService(session),
            public_base_url,
        )
        try:
            return PersistentDeviceService(
                session,
                _public_base_for(request, public_base_url),
            ).approve(payload, active)
        except AuthPolicyDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except AuthSecurityError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/auth/token-status", tags=["auth"])
    def token_status(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        authorization = request.headers.get("Authorization")
        api_key = request.headers.get("X-API-Key")
        if bool(authorization) == bool(api_key):
            raise _authentication_required(request, public_base_url)
        base = _public_base_for(request, public_base_url)
        if api_key:
            raw_token = api_key
            allowed_kinds = frozenset({"api_key"})
            required_resource = None
        else:
            raw_token = _bearer_token(request, public_base_url)
            allowed_kinds = OAUTH_ACCESS_KINDS
            required_resource = api_resource(base)
        try:
            body = PersistentOAuthService(session, base).token_status(
                raw_token,
                allowed_auth_kinds=allowed_kinds,
                required_resource=required_resource,
            )
        except AuthPolicyDenied as error:
            raise _authentication_required(request, public_base_url) from error
        return JSONResponse(body)

    @router.post("/auth/logout", tags=["auth"])
    def logout(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        service = PersistentSessionService(session)
        active = _require_browser_request(request, service, public_base_url)
        service.revoke(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"authenticated": False})
        clear_browser_session_cookies(response)
        _ = active
        return response

    return router


def _issuer_for(request: Request) -> str:
    return canonical_public_base_url(str(request.base_url))


def _public_base_for(request: Request, configured: str | None) -> str:
    return canonical_public_base_url(configured) if configured else _issuer_for(request)


def _bearer_token(request: Request, configured: str | None) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or separator != " "
        or not token
        or token != token.strip()
    ):
        raise _authentication_required(request, configured)
    return token


def _authentication_required(
    request: Request,
    configured: str | None,
) -> HTTPException:
    base = _public_base_for(request, configured)
    metadata = protected_resource_metadata_url(api_resource(base))
    return HTTPException(
        status_code=401,
        detail="OAuth Bearer token or X-API-Key is required",
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
    )


def _require_browser_request(
    request: Request,
    service: PersistentSessionService,
    configured_public_base_url: str | None,
) -> ActiveBrowserSession:
    active = service.current(request.cookies.get(SESSION_COOKIE), lock=True)
    csrf_token = request.headers.get("X-CSRF-Token")
    if active is None or not service.verify_csrf(active, csrf_token):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
    try:
        require_same_origin(
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            allowed_origin=_public_base_for(request, configured_public_base_url),
        )
    except AuthSecurityError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return active


async def _protocol_payload(request: Request) -> dict[str, object]:
    try:
        if _is_form_request(request):
            form = await request.form()
            _reject_duplicate_parameters(form.multi_items())
            return dict(form)
        payload = await request.json()
    except _OAuthRequestParsingError:
        raise
    except (RuntimeError, ValueError) as error:
        raise _OAuthRequestParsingError("malformed OAuth request body") from error
    if not isinstance(payload, dict):
        raise _OAuthRequestParsingError("OAuth request body must be an object")
    return payload


async def _command_from_request(
    request: Request,
    model: type[ProtocolCommand],
) -> ProtocolCommand:
    return model.model_validate(await _protocol_payload(request))


def _command_from_query(
    request: Request,
    model: type[ProtocolCommand],
) -> ProtocolCommand:
    items = request.query_params.multi_items()
    _reject_duplicate_parameters(items)
    return model.model_validate(dict(items))


def _reject_duplicate_parameters(items: list[tuple[str, object]]) -> None:
    seen: set[str] = set()
    for key, _ in items:
        if key in seen:
            raise _OAuthRequestParsingError("duplicate OAuth request parameter")
        seen.add(key)


def _is_form_request(request: Request) -> bool:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    return content_type == "application/x-www-form-urlencoded"


def _oauth_error(
    error: str,
    description: str,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    response = JSONResponse(
        {"error": error, "error_description": description, **(extra or {})},
        status_code=400,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


__all__ = ["SessionDependency", "create_auth_router"]
