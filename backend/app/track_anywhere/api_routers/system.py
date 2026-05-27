from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

from ..api_browser_sessions import browser_sessions
from ..api_dependencies import AuthToken
from ..api_ports.system import SystemService
from ..api_sessions import set_browser_session_cookies
from ..api_runtime import auth_cookie_secure
from ..errors import PolicyDenied
from .common import protected


router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "api_version": "v1"}


@router.get("/ready")
def ready(service: SystemService):
    try:
        payload = service.system_readiness()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "checks": {"database": "error"},
                "detail": type(exc).__name__,
            },
        )
    status_code = 200 if payload["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/system/status", dependencies=protected)
def system_status(token: AuthToken, service: SystemService, include_counts: bool = False):
    return service.system_status(token, include_counts=include_counts)


@router.post("/session/dev-local")
def create_local_session(response: Response, service: SystemService):
    try:
        session_payload = service.local_dev_session()
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    session_id, csrf_token = browser_sessions.issue(
        credential_token=session_payload["credential_token"],
        identity=session_payload["identity"],
    )
    secure_cookie = auth_cookie_secure()
    set_browser_session_cookies(response, session_id=session_id, csrf_token=csrf_token, secure=secure_cookie)
    return {
        "csrf_token": csrf_token,
        "cookie": {"http_only": True, "secure": secure_cookie, "same_site": "strict"},
    }


@router.post("/auth/dev-token")
def issue_local_dev_token(service: SystemService):
    try:
        return service.local_dev_token()
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
