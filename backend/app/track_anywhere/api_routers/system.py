from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..api_sessions import set_browser_session_cookies
from ..api_runtime import browser_sessions, service


router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "api_version": "v1"}


@router.post("/session/dev-local")
def create_local_session(response: Response):
    session_id, csrf_token = browser_sessions.issue(
        credential_token=service.owner_token,
        identity={"provider": "local", "subject": "owner", "email": None, "name": "Local Owner"},
    )
    secure_cookie = service.config.mode != "local"
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
