from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

from ..api_auth_runtime import auth_cookie_secure
from ..api_browser_sessions import browser_sessions
from ..api_dependencies import AuthToken, IdempotencyKey
from ..api_errors import raise_command_error
from ..api_ports.system import SystemService
from ..api_sessions import set_browser_session_cookies
from ..commands import PostingSemanticsRewriteCommand, PostingSemanticsReviewResolutionsCommand
from ..errors import PolicyDenied
from .common import COMMAND_ERRORS, command_payload, protected


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


@router.get("/system/posting-semantics-audit", dependencies=protected)
def posting_semantics_audit(token: AuthToken, service: SystemService, book_id: str = "book_default"):
    return service.posting_semantics_audit(token, book_id=book_id)


@router.get("/system/posting-semantics-cutover-plan", dependencies=protected)
def posting_semantics_cutover_plan(token: AuthToken, service: SystemService, book_id: str = "book_default"):
    return service.posting_semantics_cutover_plan(token, book_id=book_id)


@router.post("/system/posting-semantics-rewrite", dependencies=protected)
def rewrite_posting_semantics(
    payload: PostingSemanticsRewriteCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: SystemService,
    book_id: str = "book_default",
):
    try:
        return service.rewrite_posting_semantics(token, book_id=book_id, idempotency_key=key)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "system.posting_semantics.rewrite", recorder=service)


@router.post("/system/posting-semantics-review-resolutions", dependencies=protected)
def resolve_posting_semantics_reviews(
    payload: PostingSemanticsReviewResolutionsCommand,
    token: AuthToken,
    key: IdempotencyKey,
    service: SystemService,
    book_id: str = "book_default",
):
    try:
        return service.resolve_posting_semantics_reviews(token, command_payload(payload), book_id=book_id, idempotency_key=key)
    except COMMAND_ERRORS as exc:
        raise_command_error(exc, "system.posting_semantics.resolve", recorder=service)


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
