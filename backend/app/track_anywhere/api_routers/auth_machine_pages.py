from __future__ import annotations

from html import escape
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api_runtime import browser_sessions, service
from ..api_sessions import CSRF_COOKIE, SESSION_COOKIE
from ..errors import PolicyDenied, ValidationError
from ..platform_auth_models import DEFAULT_PLATFORM_SCOPE, parse_requested_scopes
from .auth_pages import _error, _hidden, _page
from .auth_scope_ui import actor_available_scope_text, approved_scope_text, scope_controls


router = APIRouter(prefix="/auth", tags=["auth-ui"], include_in_schema=False)


@router.get("/machine-tokens")
def machine_tokens_page(request: Request) -> HTMLResponse:
    session = _session_context(request)
    if isinstance(session, RedirectResponse):
        return session
    return _machine_tokens_form(session=session, error=None)


@router.post("/machine-tokens")
def create_machine_token(
    request: Request,
    name: Annotated[str, Form()] = "Local agent token",
    description: Annotated[str, Form()] = "",
    ttl_days: Annotated[str, Form()] = "30",
    csrf_token: Annotated[str, Form()] = "",
    approved_scope: Annotated[list[str] | None, Form()] = None,
    scope_selection_present: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    session = _session_context(request)
    if isinstance(session, RedirectResponse):
        return session
    _verify_csrf(session, csrf_token)
    try:
        scope_text = _selected_scope_text(session, approved_scope, scope_selection_present is not None)
        result, _replay = service.issue_machine_credential_command(
            session["credential"],
            {
                "name": name.strip() or "Local agent token",
                "description": description.strip(),
                "ttl_minutes": _ttl_minutes(ttl_days),
                "scopes": parse_requested_scopes(scope_text),
            },
            idempotency_key=f"auth-ui-machine-{uuid4().hex}",
        )
    except (PolicyDenied, ValidationError, ValueError) as exc:
        return _machine_tokens_form(session=session, error=str(exc), values={"name": name, "description": description, "ttl_days": ttl_days}, status_code=400)
    return _machine_tokens_form(session=session, error=None, created_token=result["token"])


@router.post("/machine-tokens/{credential_id}/revoke")
def revoke_machine_token(request: Request, credential_id: str, csrf_token: Annotated[str, Form()] = "") -> HTMLResponse:
    session = _session_context(request)
    if isinstance(session, RedirectResponse):
        return session
    _verify_csrf(session, csrf_token)
    try:
        service.revoke_credential_by_id_command(
            session["credential"],
            credential_id,
            {"reason": "revoked from auth UI"},
            idempotency_key=f"auth-ui-machine-revoke-{credential_id}-{uuid4().hex}",
        )
    except (PolicyDenied, ValidationError, ValueError) as exc:
        return _machine_tokens_form(session=session, error=str(exc), status_code=400)
    return RedirectResponse("/api/v1/auth/machine-tokens", status_code=303)


def _session_context(request: Request) -> dict[str, Any] | RedirectResponse:
    session_id = request.cookies.get(SESSION_COOKIE)
    identity = browser_sessions.identity_for(session_id)
    credential = browser_sessions.credential_for(session_id)
    csrf_token = request.cookies.get(CSRF_COOKIE)
    if identity is None or credential is None or csrf_token is None:
        next_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/api/v1/auth/login?next={quote(next_path)}", status_code=303)
    try:
        actor = service.actor_from_token(credential, "credential:write")
    except PolicyDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if actor.actor_type != "human":
        raise HTTPException(status_code=403, detail="only human owner credentials can manage machine tokens")
    return {"session_id": session_id, "identity": identity, "credential": credential, "csrf_token": csrf_token, "actor": actor}


def _verify_csrf(session: dict[str, Any], csrf_token: str) -> None:
    if not browser_sessions.verify_csrf(session["session_id"], csrf_token):
        raise HTTPException(status_code=400, detail="missing or invalid CSRF token")


def _selected_scope_text(session: dict[str, Any], approved_scope: list[str] | None, selection_present: bool) -> str:
    selected = approved_scope_text(
        requested_scope_text=DEFAULT_PLATFORM_SCOPE,
        approved_scopes=approved_scope,
        selection_present=selection_present,
    )
    available = set(parse_requested_scopes(actor_available_scope_text(session["actor"].scopes)))
    denied = set(parse_requested_scopes(selected)) - available
    if denied:
        raise ValidationError(f"scope is not available to this account: {sorted(denied)}")
    return selected


def _ttl_minutes(ttl_days: str) -> int:
    try:
        days = int(ttl_days)
    except ValueError as exc:
        raise ValidationError("expiration must be a whole number of days") from exc
    if days < 1 or days > 90:
        raise ValidationError("expiration must be between 1 and 90 days")
    return days * 24 * 60


def _machine_tokens_form(
    *,
    session: dict[str, Any],
    error: str | None,
    created_token: str | None = None,
    values: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    form_values = values or {"name": "Local agent token", "description": "", "ttl_days": "30"}
    available_scope_text = actor_available_scope_text(session["actor"].scopes)
    token_panel = _created_token_panel(created_token)
    body = f"""
      <main class="panel" style="width:min(94vw,760px)">
        <p class="eyebrow">Track Anywhere Auth</p>
        <h1>Machine tokens</h1>
        <p class="muted">Create stable tokens for local agents. The raw token is shown only once.</p>
        {token_panel}
        <section>
          <h2 style="margin:0;font-size:18px">Create token</h2>
          {_error(error)}
          <form method="post" action="/api/v1/auth/machine-tokens">
            {_hidden('csrf_token', session['csrf_token'])}
            {_hidden('scope_selection_present', '1')}
            <label>Name<input name="name" value="{escape(form_values['name'], quote=True)}" required maxlength="120"></label>
            <label>Description<textarea name="description" maxlength="240">{escape(form_values['description'])}</textarea></label>
            <label>Expires after days<input name="ttl_days" type="number" min="1" max="90" value="{escape(form_values['ttl_days'], quote=True)}" required></label>
            {scope_controls(DEFAULT_PLATFORM_SCOPE, available_scope_text=available_scope_text)}
            <button type="submit">Create machine token</button>
          </form>
        </section>
        {_token_list(session)}
        <a class="link" href="/api/v1/auth/session-view">Back to session</a>
      </main>
    """
    return _page("Machine tokens", body, status_code)


def _created_token_panel(token: str | None) -> str:
    if token is None:
        return ""
    return f"""
      <section class="scope-panel">
        <strong>Token created</strong>
        <p class="muted">Shown once. Store it in your local agent config or environment.</p>
        <textarea readonly>{escape(token)}</textarea>
      </section>
    """


def _token_list(session: dict[str, Any]) -> str:
    tokens = [item for item in service.list_agent_credentials(session["credential"]) if item["actor_type"] == "machine"]
    if not tokens:
        return "<section class='scope-panel'><strong>Existing tokens</strong><p class='muted'>No machine tokens yet.</p></section>"
    rows = "\n".join(_token_row(item, session["csrf_token"]) for item in tokens)
    return f"<section class='scope-panel'><strong>Existing tokens</strong><div class='scope-list'>{rows}</div></section>"


def _token_row(item: dict[str, Any], csrf_token: str) -> str:
    status = "active" if item["active"] else "revoked"
    scopes = ", ".join(item["scopes"])
    revoke = ""
    if item["active"]:
        revoke = f"""
          <form method="post" action="/api/v1/auth/machine-tokens/{escape(item['credential_id'], quote=True)}/revoke">
            {_hidden('csrf_token', csrf_token)}
            <button class="secondary" type="submit">Revoke</button>
          </form>
        """
    return f"""
      <article class="scope-group">
        <strong>{escape(item.get('name') or item['key_prefix'])}</strong>
        <span class="muted">{escape(item['key_prefix'])} · {status} · expires {escape(item['expires_at'])}</span>
        <span class="scope-name">{escape(scopes)}</span>
        {revoke}
      </article>
    """
