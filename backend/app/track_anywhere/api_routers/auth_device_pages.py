from __future__ import annotations

from html import escape
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api_sessions import CSRF_COOKIE, SESSION_COOKIE
from ..api_runtime import browser_sessions, platform_key_exchange, service
from ..errors import PolicyDenied, ValidationError
from ..platform_auth_models import normalize_user_code
from ..security import hash_secret
from .auth_pages import _error, _hidden, _page
from .auth_scope_ui import scope_controls


router = APIRouter(prefix="/auth", tags=["auth-ui"], include_in_schema=False)


@router.get("/device")
def device_page(request: Request, user_code: str | None = None) -> HTMLResponse:
    identity = browser_sessions.identity_for(request.cookies.get(SESSION_COOKIE))
    csrf_token = request.cookies.get(CSRF_COOKIE)
    if identity is None or csrf_token is None:
        next_path = str(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/api/v1/auth/login?next={quote(next_path)}", status_code=303)
    return _device_form(user_code=user_code or "", csrf_token=csrf_token, error=None)


@router.post("/device")
def device_approve(
    request: Request,
    user_code: Annotated[str, Form()],
    action: Annotated[str, Form()] = "approve",
    csrf_token: Annotated[str, Form()] = "",
    approved_scope: Annotated[list[str] | None, Form()] = None,
    scope_selection_present: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    session_id = request.cookies.get(SESSION_COOKIE)
    credential = browser_sessions.credential_for(session_id)
    if credential is None:
        return RedirectResponse(f"/api/v1/auth/login?next={quote('/api/v1/auth/device')}", status_code=303)
    if not browser_sessions.verify_csrf(session_id, csrf_token):
        raise HTTPException(status_code=400, detail="missing or invalid CSRF token")
    try:
        actor = service.actor_from_token(credential)
        selected_scopes = approved_scope if scope_selection_present is not None else None
        grant = platform_key_exchange.approve_device_user_code(user_code, actor, action, service.storage, approved_scopes=selected_scopes)
    except (PolicyDenied, ValidationError, ValueError) as exc:
        return _device_form(user_code=user_code, csrf_token=csrf_token, error=str(exc), status_code=400)
    verb = "Denied" if grant.status == "denied" else "Approved"
    body = f"""
      <section class="panel">
        <p class="eyebrow">Track Anywhere Device Login</p>
        <h1>{verb}</h1>
        <p class="muted">You can return to the CLI.</p>
      </section>
    """
    return _page(f"Device {verb}", body)


def _device_form(*, user_code: str, csrf_token: str, error: str | None, status_code: int = 200) -> HTMLResponse:
    scope_options = _device_scope_options(user_code)
    body = f"""
      <section class="panel">
        <p class="eyebrow">Track Anywhere Device Login</p>
        <h1>Authorize device</h1>
        <p class="muted">Enter the code shown in your terminal, then choose the access this CLI token should receive.</p>
        {_error(error)}
        <form method="post" action="/api/v1/auth/device">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
          <label>Code<input name="user_code" value="{escape(user_code, quote=True)}" required autocomplete="one-time-code"></label>
          {scope_options}
          <button name="action" value="approve" type="submit">Approve</button>
          <button class="secondary" name="action" value="deny" type="submit">Deny</button>
        </form>
      </section>
    """
    return _page("Authorize device", body, status_code)


def _device_scope_options(user_code: str) -> str:
    if not user_code.strip():
        return ""
    grant = service.storage.load_device_grant_by_user_hash(hash_secret(normalize_user_code(user_code)))
    if grant is None or grant.status != "pending":
        return ""
    return _hidden("scope_selection_present", "1") + scope_controls(" ".join(grant.scopes))
