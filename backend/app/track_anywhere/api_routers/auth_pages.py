from __future__ import annotations

from html import escape
from typing import Annotated
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api_sessions import CSRF_COOKIE, SESSION_COOKIE, set_browser_session_cookies
from ..api_runtime import auth_cookie_secure, auth_settings, browser_sessions, password_accounts, platform_key_exchange, service
from ..auth_identities import OAuthIdentity
from ..errors import PolicyDenied, ValidationError
from ..password_auth import PasswordSignupCommand
from ..platform_auth import OAuthAuthorizeCommand
from .auth_scope_ui import actor_available_scope_text, approved_scope_text, requested_scope_text, scope_controls


router = APIRouter(prefix="/auth", tags=["auth-ui"], include_in_schema=False)


@router.get("/login")
def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return _auth_form(request, mode="login", next_path=_safe_next(next), error=None)


@router.get("/signup")
def signup_page(request: Request, next: str | None = None) -> HTMLResponse:
    return _auth_form(request, mode="signup", next_path=_safe_next(next), error=None)


@router.post("/password/login/form")
def login_form(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/api/v1/auth/session-view",
):
    try:
        account = password_accounts.authenticate(email=email, password=password)
    except PolicyDenied as exc:
        service.record_security_failure("auth.password_denied", {"reason": "bad_credentials"})
        return _auth_form(request, mode="login", next_path=_safe_next(next), error="Email or password is incorrect.", status_code=401)
    return _issue_password_session(email=account.email, display_name=account.display_name, role=account.role, next_path=next)


@router.post("/password/signup/form")
def signup_form(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    display_name: Annotated[str | None, Form()] = None,
    next: Annotated[str, Form()] = "/api/v1/auth/session-view",
):
    payload = PasswordSignupCommand(email=email, password=password, display_name=display_name)
    if service.config.mode != "local" and payload.email not in auth_settings.password_signup_allowed_emails:
        service.record_security_failure("auth.password_signup_denied", {"reason": "email_not_allowlisted"})
        return _auth_form(request, mode="signup", next_path=_safe_next(next), error="Password signup is not allowlisted.", status_code=403)
    try:
        account = password_accounts.create(email=payload.email, password=payload.password, display_name=payload.display_name)
    except ValidationError:
        return _auth_form(request, mode="signup", next_path=_safe_next(next), error="Email is already registered.", status_code=409)
    return _issue_password_session(email=account.email, display_name=account.display_name, role=account.role, next_path=next)


@router.get("/session-view")
def session_view(request: Request) -> HTMLResponse:
    identity = browser_sessions.identity_for(request.cookies.get(SESSION_COOKIE))
    if identity is None:
        return RedirectResponse(f"/api/v1/auth/login?next={quote('/api/v1/auth/session-view')}", status_code=303)
    name = identity.get("name") or identity.get("display_name") or identity.get("email") or "Signed in"
    body = f"""
      <section class="panel">
        <p class="eyebrow">Track Anywhere</p>
        <h1>{escape(str(name))}</h1>
        <p class="muted">You are signed in. You can return to the CLI or continue using the API.</p>
        <form method="post" action="/api/v1/auth/logout"><button type="submit">Sign out</button></form>
      </section>
    """
    return _page("Signed in", body)


@router.get("/callback")
def cli_callback_page(request: Request) -> HTMLResponse:
    if request.query_params.get("code") or request.query_params.get("error"):
        return _callback_result(str(request.url))

    missing = [name for name in ("client_id", "redirect_uri", "state", "code_challenge") if not request.query_params.get(name)]
    if missing:
        return _page("Incomplete link", f"<section class='panel'><h1>Incomplete link</h1><p class='error'>Missing {escape(', '.join(missing))}.</p></section>", 400)

    session_id = request.cookies.get(SESSION_COOKIE)
    identity = browser_sessions.identity_for(session_id)
    credential = browser_sessions.credential_for(session_id)
    csrf_token = request.cookies.get(CSRF_COOKIE)
    if identity is None or credential is None or csrf_token is None:
        next_path = _request_path(request)
        return RedirectResponse(f"/api/v1/auth/login?next={quote(next_path)}", status_code=303)
    actor = service.actor_from_token(credential)
    return _approval_form(request, identity=identity, csrf_token=csrf_token, error=None, available_scope_text=actor_available_scope_text(actor.scopes))


@router.post("/callback")
def cli_callback_approve(
    request: Request,
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    scope: Annotated[str, Form()],
    state: Annotated[str | None, Form()],
    code_challenge: Annotated[str, Form()],
    code_challenge_method: Annotated[str, Form()] = "S256",
    action: Annotated[str, Form()] = "approve",
    csrf_token: Annotated[str, Form()] = "",
    approved_scope: Annotated[list[str] | None, Form()] = None,
    scope_selection_present: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    session_id = request.cookies.get(SESSION_COOKIE)
    credential = browser_sessions.credential_for(session_id)
    identity = browser_sessions.identity_for(session_id)
    if credential is None or identity is None:
        return RedirectResponse(f"/api/v1/auth/login?next={quote(_request_path(request))}", status_code=303)
    if not browser_sessions.verify_csrf(session_id, csrf_token):
        raise HTTPException(status_code=400, detail="missing or invalid CSRF token")

    form_values = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state or "",
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }
    available_scope_text = None
    try:
        actor = service.actor_from_token(credential)
        available_scope_text = actor_available_scope_text(actor.scopes)
        approved_scope_value = approved_scope_text(
            requested_scope_text=scope,
            approved_scopes=approved_scope,
            selection_present=scope_selection_present is not None,
        )
        result = platform_key_exchange.authorize(
            OAuthAuthorizeCommand(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scope=approved_scope_value,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                action=action,
            ),
            actor,
            service.storage,
        )
    except (PolicyDenied, ValidationError, ValueError) as exc:
        return _approval_form(request, identity=identity, csrf_token=csrf_token, error=str(exc), status_code=400, values=form_values, available_scope_text=available_scope_text)
    return _callback_delivery(result["redirect_uri"])


def _issue_password_session(*, email: str, display_name: str, role: str, next_path: str):
    login = service.login_oauth_identity(
        OAuthIdentity(provider="password", subject=email, email=email, email_verified=True, name=display_name, picture=None),
        role=role,
    )
    session_identity = {**login["identity"], "role": login["membership"]["role"]}
    session_id, csrf_token = browser_sessions.issue(credential_token=login["credential_token"], identity=session_identity)
    response = RedirectResponse(_safe_next(next_path), status_code=303)
    set_browser_session_cookies(response, session_id=session_id, csrf_token=csrf_token, secure=auth_cookie_secure())
    return response


def _auth_form(request: Request, *, mode: str, next_path: str, error: str | None, status_code: int = 200) -> HTMLResponse:
    is_signup = mode == "signup"
    title = "Create your account" if is_signup else "Sign in"
    action = "/api/v1/auth/password/signup/form" if is_signup else "/api/v1/auth/password/login/form"
    alternate = "login" if is_signup else "signup"
    fields = """
      <label>Name<input name="display_name" autocomplete="name"></label>
    """ if is_signup else ""
    providers = "".join(
        f"<a class='secondary' href='/api/v1/auth/oauth/{escape(provider.name)}/authorize?next={quote(next_path)}'>Continue with {escape(provider.display_name)}</a>"
        for provider in auth_settings.providers
    )
    body = f"""
      <section class="panel">
        <p class="eyebrow">Track Anywhere</p>
        <h1>{title}</h1>
        {_error(error)}
        <form method="post" action="{action}">
          <input type="hidden" name="next" value="{escape(next_path, quote=True)}">
          {fields}
          <label>Email<input name="email" type="email" autocomplete="email" required></label>
          <label>Password<input name="password" type="password" autocomplete="{'new-password' if is_signup else 'current-password'}" minlength="8" required></label>
          <button type="submit">{title}</button>
        </form>
        <div class="stack">{providers}</div>
        <a class="link" href="/api/v1/auth/{alternate}?next={quote(next_path)}">{'I already have an account' if is_signup else 'Create an account'}</a>
      </section>
    """
    return _page(title, body, status_code)


def _approval_form(
    request: Request,
    *,
    identity: dict,
    csrf_token: str,
    error: str | None,
    status_code: int = 200,
    values: dict[str, str] | None = None,
    available_scope_text: str | None = None,
) -> HTMLResponse:
    name = identity.get("name") or identity.get("display_name") or identity.get("email") or "this account"
    field_values = values or {key: request.query_params.get(key, "") for key in ("client_id", "redirect_uri", "scope", "state", "code_challenge", "code_challenge_method")}
    hidden = "".join(
        _hidden(name, field_values.get(name, ""))
        for name in ("client_id", "redirect_uri", "scope", "state", "code_challenge", "code_challenge_method")
    )
    scope_options = scope_controls(requested_scope_text(field_values.get("scope")), available_scope_text=available_scope_text)
    body = f"""
      <section class="panel">
        <p class="eyebrow">Track Anywhere CLI</p>
        <h1>Connect command line</h1>
        <p class="muted">Signed in as {escape(str(name))}. Choose the access this CLI token should receive, then approve the request.</p>
        {_error(error)}
        <form method="post" action="/api/v1/auth/callback">
          {hidden}
          {_hidden('csrf_token', csrf_token)}
          {_hidden('scope_selection_present', '1')}
          {scope_options}
          <button name="action" value="approve" type="submit">Approve</button>
          <button class="secondary" name="action" value="deny" type="submit">Deny</button>
        </form>
      </section>
    """
    return _page("Connect CLI", body, status_code)


def _callback_result(callback_url: str) -> HTMLResponse:
    body = f"""
      <section class="panel">
        <p class="eyebrow">Track Anywhere CLI</p>
        <h1>Paste this callback URL</h1>
        <textarea readonly>{escape(callback_url)}</textarea>
      </section>
    """
    return _page("CLI callback", body)


def _callback_delivery(callback_url: str) -> HTMLResponse | RedirectResponse:
    parsed = urlparse(callback_url)
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.path == "/callback":
        return RedirectResponse(callback_url, status_code=303)
    return _callback_result(callback_url)


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} | Track Anywhere</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f4ef;color:#16201d;font-family:Inter,ui-sans-serif,system-ui,sans-serif}}
.panel{{width:min(92vw,430px);display:grid;gap:18px}}.eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#59645f}}
h1{{margin:0;font-size:32px;line-height:1.05}}form,.stack{{display:grid;gap:12px}}label{{display:grid;gap:6px;font-size:14px;color:#33413c}}
input,textarea{{box-sizing:border-box;width:100%;border:1px solid #c8d0ca;border-radius:8px;background:#fff;padding:11px 12px;font:inherit;color:#16201d}}
textarea{{min-height:140px;resize:vertical}}button,.secondary{{border:1px solid #16201d;border-radius:8px;background:#16201d;color:#fff;padding:11px 14px;font:inherit;text-align:center;text-decoration:none;cursor:pointer}}
.secondary{{background:#fff;color:#16201d}}.link{{color:#16201d}}.muted{{color:#59645f;line-height:1.5}}.error{{color:#a3332a}}
.scope-panel{{border:1px solid #d7ddd8;border-radius:8px;padding:14px;display:grid;gap:10px}}.scope-panel legend{{padding:0 6px;font-weight:650}}
.scope-list{{display:grid;gap:8px}}.scope-option{{display:flex;align-items:center;gap:9px}}.scope-option input{{width:auto;margin:0}}.scope-name{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}}.scope-group{{display:grid;gap:7px;border-top:1px solid #e2e6e2;padding-top:10px}}.scope-group-label{{font-weight:650}}.scope-group-items{{padding-left:23px}}
</style><script>
document.addEventListener("DOMContentLoaded",()=>{{const all=document.querySelector("[data-scope-all]");const boxes=[...document.querySelectorAll("input[name='approved_scope']")];const groups=[...document.querySelectorAll("[data-scope-group]")];if(!all||!boxes.length)return;const syncBox=(box,items)=>{{box.checked=items.every(item=>item.checked);box.indeterminate=!box.checked&&items.some(item=>item.checked)}};const sync=()=>{{syncBox(all,boxes);groups.forEach(group=>syncBox(group,boxes.filter(box=>box.dataset.scopeItem===group.dataset.scopeGroup)))}};all.addEventListener("change",()=>{{boxes.forEach(box=>box.checked=all.checked);sync()}});groups.forEach(group=>group.addEventListener("change",()=>{{boxes.filter(box=>box.dataset.scopeItem===group.dataset.scopeGroup).forEach(box=>box.checked=group.checked);sync()}}));boxes.forEach(box=>box.addEventListener("change",sync));sync()}});
</script></head><body>{body}</body></html>""", status_code=status_code)


def _hidden(name: str, value: str | None) -> str:
    return f'<input type="hidden" name="{escape(name, quote=True)}" value="{escape(value or "", quote=True)}">'


def _error(error: str | None) -> str:
    return f"<p class='error'>{escape(error)}</p>" if error else ""


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/api/v1/auth/session-view"


def _request_path(request: Request) -> str:
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")
