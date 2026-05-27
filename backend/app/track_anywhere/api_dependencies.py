from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from .api_browser_sessions import browser_sessions
from .api_sessions import SESSION_COOKIE
from .api_runtime import ALLOWED_ORIGINS, service as runtime_service
from .errors import SecurityPreconditionFailed
from .security import CredentialReference, validate_web_security


AuthorizationHeader = Annotated[str | None, Header()]
ApiKeyHeader = Annotated[str | None, Header(alias="X-API-Key")]
IdempotencyHeader = Annotated[str | None, Header()]
CsrfHeader = Annotated[str | None, Header()]
OriginHeader = Annotated[str | None, Header()]
RefererHeader = Annotated[str | None, Header()]


def allowed_origin_for_request(origin: str | None, referer: str | None) -> str:
    if origin in ALLOWED_ORIGINS:
        return origin
    for allowed_origin in ALLOWED_ORIGINS:
        if referer and referer.startswith(allowed_origin):
            return allowed_origin
    return ALLOWED_ORIGINS[0]


def token_from_request(
    request: Request,
    authorization: AuthorizationHeader = None,
    x_api_key: ApiKeyHeader = None,
) -> str | CredentialReference:
    if x_api_key:
        token = x_api_key.strip()
        if token:
            return token

    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="invalid authorization header")
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            return token

    session_credential = browser_sessions.credential_for(request.cookies.get(SESSION_COOKIE))
    if session_credential:
        return session_credential

    raise HTTPException(status_code=401, detail="missing bearer token or session")


def idempotency_key(x_idempotency_key: IdempotencyHeader = None) -> str:
    if not x_idempotency_key:
        raise HTTPException(status_code=400, detail="missing idempotency key")
    return x_idempotency_key


def get_service():
    return runtime_service


def session_guard(
    request: Request,
    x_csrf_token: CsrfHeader = None,
    origin: OriginHeader = None,
    referer: RefererHeader = None,
) -> None:
    session_id = request.cookies.get(SESSION_COOKIE)
    auth_mode = "session" if session_id else "bearer"
    is_mutating = request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    if session_id and is_mutating and not browser_sessions.verify_csrf(session_id, x_csrf_token):
        runtime_service.record_security_failure("security.csrf_denied", {"path": request.url.path, "origin": origin})
        raise HTTPException(status_code=400, detail="missing or invalid CSRF token")
    allowed_origin = allowed_origin_for_request(origin, referer)
    if is_mutating and auth_mode == "bearer":
        origin_ok = origin in ALLOWED_ORIGINS if origin else True
        referer_ok = any(referer and referer.startswith(item) for item in ALLOWED_ORIGINS)
        if not origin_ok or (referer and not referer_ok):
            runtime_service.record_security_failure(
                "security.origin_denied",
                {"path": request.url.path, "origin": origin, "referer": referer},
            )
            raise HTTPException(status_code=400, detail="missing or invalid Origin/Referer")
    try:
        validate_web_security(
            method=request.method,
            auth_mode=auth_mode,
            csrf_token="verified" if auth_mode == "session" else None,
            expected_csrf_token="verified" if auth_mode == "session" else None,
            origin=origin,
            referer=referer,
            allowed_origin=allowed_origin,
        )
    except SecurityPreconditionFailed as exc:
        runtime_service.record_security_failure(
            "security.origin_denied",
            {"path": request.url.path, "origin": origin, "referer": referer},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


AuthToken = Annotated[str | CredentialReference, Depends(token_from_request)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
SessionGuard = Depends(session_guard)
