from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from .click_common import Requester
from .config import CliConfig


DEFAULT_PLATFORM_CLIENT_ID = "track-anywhere-web"
DEFAULT_WEB_URL = "http://127.0.0.1:8000"
DEFAULT_CLI_SCOPE = "account:read book:read ledger:read"


@dataclass(frozen=True)
class BrowserLoginRequest:
    auth_url: str
    client_id: str
    redirect_uri: str
    scope: str
    state: str
    code_verifier: str


@dataclass(frozen=True)
class DeviceLoginRequest:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int
    client_id: str


def create_browser_login_request(
    *,
    web_url: str = DEFAULT_WEB_URL,
    client_id: str = DEFAULT_PLATFORM_CLIENT_ID,
    scope: str = DEFAULT_CLI_SCOPE,
    redirect_uri: str | None = None,
) -> BrowserLoginRequest:
    base_web_url = web_url.rstrip("/")
    auth_endpoint = f"{base_web_url}/api/v1/auth/callback"
    redirect_uri = redirect_uri or auth_endpoint
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
        auth_url=f"{auth_endpoint}?{query}",
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_verifier=verifier,
    )


def create_device_login_request(
    *,
    config: CliConfig,
    requester: Requester,
    client_id: str = DEFAULT_PLATFORM_CLIENT_ID,
    scope: str = DEFAULT_CLI_SCOPE,
) -> tuple[int, dict, DeviceLoginRequest | None]:
    status, data = requester(
        CliConfig(base_url=config.base_url, token=None, insecure_automation=config.insecure_automation),
        "POST",
        "/api/v1/oauth/device/authorize",
        {"client_id": client_id, "scope": scope},
        None,
    )
    if status >= 400 or not isinstance(data, dict):
        return status, data if isinstance(data, dict) else {"detail": data}, None
    request = DeviceLoginRequest(
        device_code=str(data["device_code"]),
        user_code=str(data["user_code"]),
        verification_uri=str(data["verification_uri"]),
        verification_uri_complete=str(data.get("verification_uri_complete") or data["verification_uri"]),
        expires_in=int(data["expires_in"]),
        interval=int(data["interval"]),
        client_id=client_id,
    )
    return status, data, request


def exchange_device_code_for_token(
    *,
    request: DeviceLoginRequest,
    config: CliConfig,
    requester: Requester,
    sleep=time.sleep,
) -> tuple[int, dict]:
    deadline = time.monotonic() + request.expires_in
    interval = request.interval
    while time.monotonic() < deadline:
        status, data = requester(
            CliConfig(base_url=config.base_url, token=None, insecure_automation=config.insecure_automation),
            "POST",
            "/api/v1/oauth/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": request.device_code,
                "client_id": request.client_id,
            },
            None,
        )
        payload = data if isinstance(data, dict) else {"detail": data}
        if status < 400:
            return status, payload
        error = str(payload.get("error") or payload.get("detail") or "")
        if error == "slow_down":
            interval = int(payload.get("interval") or interval + 5)
        elif error != "authorization_pending":
            return status, payload
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep(min(interval, max(0, remaining)))
    return 400, {"error": "expired_token", "error_description": "device authorization expired"}


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
