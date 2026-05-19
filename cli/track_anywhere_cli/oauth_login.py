from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from .click_common import Requester
from .config import CliConfig


DEFAULT_PLATFORM_CLIENT_ID = "track-anywhere-web"
DEFAULT_WEB_URL = "http://127.0.0.1:3000"
DEFAULT_CLI_SCOPE = "account:read book:read ledger:read"


@dataclass(frozen=True)
class BrowserLoginRequest:
    auth_url: str
    client_id: str
    redirect_uri: str
    scope: str
    state: str
    code_verifier: str


def create_browser_login_request(
    *,
    web_url: str = DEFAULT_WEB_URL,
    client_id: str = DEFAULT_PLATFORM_CLIENT_ID,
    scope: str = DEFAULT_CLI_SCOPE,
) -> BrowserLoginRequest:
    base_web_url = web_url.rstrip("/")
    redirect_uri = f"{base_web_url}/auth/callback"
    state = _random_base64_url(18)
    verifier = _random_base64_url(48)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return BrowserLoginRequest(
        auth_url=f"{redirect_uri}?{query}",
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_verifier=verifier,
    )


def exchange_callback_for_token(
    *,
    request: BrowserLoginRequest,
    callback_value: str,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, dict]:
    code = callback_code(callback_value, expected_state=request.state)
    status, data = requester(
        CliConfig(base_url=config.base_url, token=None, insecure_automation=config.insecure_automation),
        "POST",
        "/api/v1/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
            "code_verifier": request.code_verifier,
        },
        None,
    )
    return status, data if isinstance(data, dict) else {"detail": data}


def callback_code(callback_value: str, *, expected_state: str) -> str:
    text = callback_value.strip()
    parsed = urlparse(text)
    query = parsed.query or text
    params = parse_qs(query, keep_blank_values=False)
    state = _first(params.get("state"))
    if state != expected_state:
        raise ValueError("callback state did not match this CLI login attempt")
    code = _first(params.get("code"))
    if not code:
        error = _first(params.get("error")) or "missing authorization code"
        raise ValueError(error)
    return code


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _random_base64_url(byte_length: int) -> str:
    return secrets.token_urlsafe(byte_length)


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    return values[0]
