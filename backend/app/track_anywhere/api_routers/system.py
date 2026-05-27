from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

from ..api_dependencies import AuthToken
from ..api_sessions import set_browser_session_cookies
from ..api_runtime import auth_cookie_secure, browser_sessions, service
from .common import protected


router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "api_version": "v1"}


@router.get("/ready")
def ready():
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
def system_status(token: AuthToken, include_counts: bool = False):
    return service.system_status(token, include_counts=include_counts)


@router.post("/session/dev-local")
def create_local_session(response: Response):
    if service.config.mode != "local":
        raise HTTPException(status_code=403, detail="dev session is only available in local mode")
    session_id, csrf_token = browser_sessions.issue(
        credential_token=service.owner_token,
        identity={"provider": "local", "subject": "owner", "email": None, "name": "Local Owner"},
    )
    secure_cookie = auth_cookie_secure()
    set_browser_session_cookies(response, session_id=session_id, csrf_token=csrf_token, secure=secure_cookie)
    return {
        "csrf_token": csrf_token,
        "cookie": {"http_only": True, "secure": secure_cookie, "same_site": "strict"},
    }


@router.post("/auth/dev-token")
def issue_local_dev_token():
    if service.config.mode != "local":
        raise HTTPException(status_code=403, detail="dev token is only available in local mode")
    actor = service.actor_from_token(service.owner_token)
    return {
        "token": service.owner_token,
        "actor": {
            "actor_id": actor.actor_id,
            "actor_type": actor.actor_type,
            "scopes": sorted(actor.scopes),
        },
    }
