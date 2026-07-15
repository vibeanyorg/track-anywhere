from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .click_common import Requester
from .config import (
    AuthProfile,
    CliConfig,
    RequestCredential,
    TokenResolution,
    canonical_base_url,
    canonical_resource,
    validate_transport_url,
)
from .http import FormPayload


DEFAULT_PLATFORM_CLIENT_ID = "track-anywhere-cli"
DEFAULT_WEB_URL = "http://127.0.0.1:8000"
DEFAULT_CLI_SCOPE = "book:read ledger:read"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_REDIRECT_PLACEHOLDER = "http://127.0.0.1/callback"
OAuthForm = FormPayload


@dataclass(frozen=True)
class OAuthMetadata:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    device_authorization_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource", canonical_resource(self.resource))
        object.__setattr__(self, "issuer", canonical_base_url(self.issuer))
        for field_name in (
            "authorization_endpoint",
            "token_endpoint",
            "device_authorization_endpoint",
            "registration_endpoint",
            "revocation_endpoint",
        ):
            value = validate_transport_url(str(getattr(self, field_name)))
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True)
class BrowserLoginRequest:
    auth_url: str
    client_id: str
    redirect_uri: str
    scope: str
    resource: str
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
    resource: str | None = None


def discover_oauth_metadata(
    *,
    base_url: str,
    resource: str | None,
    requester: Requester,
) -> OAuthMetadata:
    canonical_base = canonical_base_url(base_url)
    requested_resource = canonical_resource(resource or f"{canonical_base}/api/v2")
    resource_parts = urlsplit(requested_resource)
    resource_origin = urlunsplit(
        (resource_parts.scheme, resource_parts.netloc, "", "", "")
    )
    protected_endpoints = [
        f"{resource_origin}/.well-known/oauth-protected-resource{resource_parts.path}",
        f"{resource_origin}/.well-known/oauth-protected-resource",
        f"{canonical_base}/api/v2/oauth/protected-resource",
    ]
    status, protected = _first_metadata_response(
        endpoints=protected_endpoints,
        label="protected resource metadata",
        resource=requested_resource,
        requester=requester,
    )
    if status >= 400 or not isinstance(protected, dict):
        raise ValueError(
            _metadata_error("protected resource metadata", status, protected)
        )
    discovered_resource = canonical_resource(
        _required_metadata_url(protected, "resource")
    )
    if discovered_resource != requested_resource:
        raise ValueError(
            "protected resource metadata did not match the requested resource"
        )
    authorization_servers = protected.get("authorization_servers")
    if not isinstance(authorization_servers, list) or not authorization_servers:
        raise ValueError("protected resource metadata omitted authorization_servers")
    authorization_server = canonical_base_url(str(authorization_servers[0]))
    validate_transport_url(authorization_server)

    authorization_parts = urlsplit(authorization_server)
    authorization_origin = urlunsplit(
        (authorization_parts.scheme, authorization_parts.netloc, "", "", "")
    )
    authorization_path = authorization_parts.path.rstrip("/")
    status, authorization = _first_metadata_response(
        endpoints=[
            f"{authorization_origin}/.well-known/oauth-authorization-server{authorization_path}",
            f"{authorization_server}/api/v2/oauth/authorization-server",
        ],
        label="authorization server metadata",
        resource=discovered_resource,
        requester=requester,
    )
    if status >= 400 or not isinstance(authorization, dict):
        raise ValueError(
            _metadata_error("authorization server metadata", status, authorization)
        )
    issuer = canonical_base_url(_required_metadata_url(authorization, "issuer"))
    if issuer != authorization_server:
        raise ValueError("authorization server metadata issuer did not match discovery")
    challenge_methods = authorization.get("code_challenge_methods_supported")
    if not isinstance(challenge_methods, list) or "S256" not in challenge_methods:
        raise ValueError("authorization server does not advertise PKCE S256")
    endpoint_auth = authorization.get("token_endpoint_auth_methods_supported")
    if not isinstance(endpoint_auth, list) or "none" not in endpoint_auth:
        raise ValueError("authorization server does not support public clients")

    return OAuthMetadata(
        resource=discovered_resource,
        issuer=issuer,
        authorization_endpoint=_required_metadata_url(
            authorization, "authorization_endpoint"
        ),
        token_endpoint=_required_metadata_url(authorization, "token_endpoint"),
        device_authorization_endpoint=_required_metadata_url(
            authorization, "device_authorization_endpoint"
        ),
        registration_endpoint=_required_metadata_url(
            authorization, "registration_endpoint"
        ),
        revocation_endpoint=_required_metadata_url(
            authorization, "revocation_endpoint"
        ),
    )


def register_public_client(
    *,
    metadata: OAuthMetadata,
    redirect_uri: str,
    scope: str,
    requester: Requester,
    grant_types: tuple[str, ...] = ("authorization_code", "refresh_token"),
) -> str:
    redirect_uri = validate_transport_url(redirect_uri)
    status, data = _request_endpoint(
        endpoint=metadata.registration_endpoint,
        method="POST",
        payload={
            "client_name": "Track Anywhere CLI",
            "redirect_uris": [redirect_uri],
            "scope": scope,
            "grant_types": list(grant_types),
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        resource=metadata.resource,
        requester=requester,
    )
    if status >= 400 or not isinstance(data, dict):
        raise ValueError(_metadata_error("OAuth client registration", status, data))
    client_id = data.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise ValueError("OAuth client registration omitted client_id")
    return client_id


def create_browser_login_request(
    *,
    metadata: OAuthMetadata | None = None,
    web_url: str = DEFAULT_WEB_URL,
    client_id: str = DEFAULT_PLATFORM_CLIENT_ID,
    scope: str = DEFAULT_CLI_SCOPE,
    redirect_uri: str | None = None,
) -> BrowserLoginRequest:
    if metadata is None:
        base_web_url = canonical_base_url(web_url)
        metadata = OAuthMetadata(
            resource=f"{base_web_url}/api/v2",
            issuer=base_web_url,
            authorization_endpoint=f"{base_web_url}/auth/authorize",
            token_endpoint=f"{base_web_url}/api/v2/oauth/token",
            device_authorization_endpoint=f"{base_web_url}/api/v2/oauth/device/authorize",
            registration_endpoint=f"{base_web_url}/api/v2/oauth/register",
            revocation_endpoint=f"{base_web_url}/api/v2/oauth/revoke",
        )
    redirect_uri = validate_transport_url(redirect_uri or DEVICE_REDIRECT_PLACEHOLDER)
    state = _random_base64_url(18)
    verifier = _random_base64_url(48)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "resource": metadata.resource,
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in metadata.authorization_endpoint else "?"
    return BrowserLoginRequest(
        auth_url=f"{metadata.authorization_endpoint}{separator}{query}",
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        resource=metadata.resource,
        state=state,
        code_verifier=verifier,
    )


def create_device_login_request(
    *,
    requester: Requester,
    client_id: str = DEFAULT_PLATFORM_CLIENT_ID,
    scope: str = DEFAULT_CLI_SCOPE,
    metadata: OAuthMetadata | None = None,
    config: CliConfig | None = None,
) -> tuple[int, dict, DeviceLoginRequest | None]:
    if metadata is not None:
        endpoint = metadata.device_authorization_endpoint
        resource = metadata.resource
    elif config is not None:
        endpoint = f"{config.base_url}/api/v2/oauth/device/authorize"
        resource = config.resource
    else:
        raise ValueError("device authorization requires OAuth metadata")
    body: dict[str, str] = {"client_id": client_id, "scope": scope}
    if resource:
        body["resource"] = resource
    status, data = _request_endpoint(
        endpoint=endpoint,
        method="POST",
        payload=body,
        resource=resource,
        requester=requester,
    )
    if status >= 400 or not isinstance(data, dict):
        return status, data if isinstance(data, dict) else {"detail": data}, None
    try:
        request = DeviceLoginRequest(
            device_code=_required_text(data, "device_code"),
            user_code=_required_text(data, "user_code"),
            verification_uri=validate_transport_url(
                _required_text(data, "verification_uri")
            ),
            verification_uri_complete=validate_transport_url(
                str(data.get("verification_uri_complete") or data["verification_uri"])
            ),
            expires_in=int(data["expires_in"]),
            interval=int(data["interval"]),
            client_id=client_id,
            resource=resource,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return 502, {"detail": f"invalid device authorization response: {exc}"}, None
    return status, data, request


def exchange_device_code_for_token(
    *,
    request: DeviceLoginRequest,
    requester: Requester,
    metadata: OAuthMetadata | None = None,
    config: CliConfig | None = None,
    sleep=time.sleep,
) -> tuple[int, dict]:
    if metadata is not None:
        endpoint = metadata.token_endpoint
        resource = metadata.resource
    elif config is not None:
        endpoint = f"{config.base_url}/api/v2/oauth/token"
        resource = request.resource or config.resource
    else:
        raise ValueError("device token exchange requires OAuth metadata")
    deadline = time.monotonic() + request.expires_in
    interval = max(1, request.interval)
    while time.monotonic() < deadline:
        form = OAuthForm(
            {
                "grant_type": DEVICE_GRANT_TYPE,
                "device_code": request.device_code,
                "client_id": request.client_id,
            }
        )
        if resource:
            form["resource"] = resource
        status, data = _request_endpoint(
            endpoint=endpoint,
            method="POST",
            payload=form,
            resource=resource,
            requester=requester,
        )
        response = data if isinstance(data, dict) else {"detail": data}
        if status < 400:
            return status, response
        error = str(response.get("error") or response.get("detail") or "")
        if error == "slow_down":
            interval = int(response.get("interval") or interval + 5)
        elif error != "authorization_pending":
            return status, response
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep(min(interval, max(0, remaining)))
    return 400, {
        "error": "expired_token",
        "error_description": "device authorization expired",
    }


def exchange_callback_for_token(
    *,
    request: BrowserLoginRequest,
    callback_value: str,
    requester: Requester,
    metadata: OAuthMetadata | None = None,
    config: CliConfig | None = None,
) -> tuple[int, dict]:
    code = callback_code(
        callback_value,
        expected_state=request.state,
        expected_redirect_uri=request.redirect_uri,
    )
    if metadata is not None:
        endpoint = metadata.token_endpoint
        resource = metadata.resource
    elif config is not None:
        endpoint = f"{config.base_url}/api/v2/oauth/token"
        resource = request.resource or config.resource
    else:
        raise ValueError("authorization code exchange requires OAuth metadata")
    form = OAuthForm(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
            "code_verifier": request.code_verifier,
            "resource": resource,
        }
    )
    status, data = _request_endpoint(
        endpoint=endpoint,
        method="POST",
        payload=form,
        resource=resource,
        requester=requester,
    )
    return status, data if isinstance(data, dict) else {"detail": data}


def callback_code(
    callback_value: str,
    *,
    expected_state: str,
    expected_redirect_uri: str | None = None,
) -> str:
    text = callback_value.strip()
    parsed = urlsplit(text)
    if expected_redirect_uri is not None:
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("callback must be an absolute redirect URI")
        if not _same_redirect_target(parsed, urlsplit(expected_redirect_uri)):
            raise ValueError(
                "callback redirect URI did not match this CLI login attempt"
            )
        query = parsed.query
    else:
        query = parsed.query or text
    params = parse_qs(query, keep_blank_values=False)
    states = params.get("state", [])
    if len(states) != 1:
        raise ValueError("callback must contain exactly one state value")
    if states[0] != expected_state:
        raise ValueError("callback state did not match this CLI login attempt")
    codes = params.get("code", [])
    if len(codes) > 1:
        raise ValueError("callback must contain exactly one authorization code")
    if not codes:
        errors = params.get("error", [])
        error = errors[0] if len(errors) == 1 else "missing authorization code"
        raise ValueError(error)
    return codes[0]


def profile_from_token_response(
    *,
    base_url: str,
    metadata: OAuthMetadata,
    client_id: str,
    token_data: dict,
    auth_kind: str,
    now: float | None = None,
    previous_refresh_token: str | None = None,
) -> AuthProfile:
    access_token, refresh_token, expires_at, scope = _token_response_values(
        token_data,
        now=now,
        previous_refresh_token=previous_refresh_token,
    )
    return AuthProfile(
        base_url=base_url,
        resource=metadata.resource,
        issuer=metadata.issuer,
        client_id=client_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scope=scope,
        token_endpoint=metadata.token_endpoint,
        revocation_endpoint=metadata.revocation_endpoint,
        auth_kind=auth_kind,
    )


def refresh_token_resolution(
    resolution: TokenResolution,
    *,
    requester: Requester,
    now: float | None = None,
    leeway_seconds: int = 60,
) -> TokenResolution:
    profile = resolution.profile
    current_time = time.time() if now is None else now
    if (
        profile is None
        or profile.expires_at is None
        or profile.expires_at > current_time + leeway_seconds
    ):
        return resolution
    if resolution.store is None:
        raise RuntimeError("OAuth profile store is unavailable")
    with resolution.store.profile_lock():
        stored = resolution.store.load_profile_with_source()
        if stored is None:
            raise RuntimeError("OAuth profile disappeared; run `ta auth login` again")
        profile = stored.profile
        latest_resolution = replace(
            resolution,
            token=profile.access_token,
            credential=RequestCredential(kind="oauth", secret=profile.access_token),
            profile=profile,
            source=stored.source,
        )
        if (
            profile.expires_at is None
            or profile.expires_at > current_time + leeway_seconds
        ):
            return latest_resolution
        if not profile.refresh_token or not profile.token_endpoint:
            raise RuntimeError("OAuth access token expired; run `ta auth login` again")
        resolution.store._begin_profile_transition_locked()
        status, data = _request_endpoint(
            endpoint=profile.token_endpoint,
            method="POST",
            payload=OAuthForm(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": profile.refresh_token,
                    "client_id": profile.client_id,
                    "resource": profile.resource,
                }
            ),
            resource=profile.resource,
            requester=requester,
        )
        if status >= 400 or not isinstance(data, dict):
            raise RuntimeError(_metadata_error("OAuth token refresh", status, data))
        try:
            access_token, refresh_token, expires_at, scope = _token_response_values(
                data,
                now=current_time,
                previous_refresh_token=profile.refresh_token,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        refreshed = replace(
            profile,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope or profile.scope,
        )
        diagnostics = [
            *resolution.diagnostics,
            *resolution.store._save_profile_locked(refreshed),
        ]
        return replace(
            latest_resolution,
            token=refreshed.access_token,
            credential=RequestCredential(kind="oauth", secret=refreshed.access_token),
            profile=refreshed,
            diagnostics=diagnostics,
        )


def revoke_oauth_profile(
    profile: AuthProfile,
    *,
    requester: Requester,
) -> tuple[bool, int, object]:
    if not profile.revocation_endpoint:
        return False, 400, {"detail": "OAuth profile has no revocation endpoint"}
    credentials = []
    if profile.refresh_token:
        credentials.append((profile.refresh_token, "refresh_token"))
    credentials.append((profile.access_token, "access_token"))
    last_status = 200
    last_data: object = {"revoked": True}
    all_revoked = True
    for token, token_type_hint in credentials:
        status, data = _request_endpoint(
            endpoint=profile.revocation_endpoint,
            method="POST",
            payload=OAuthForm(
                {
                    "token": token,
                    "token_type_hint": token_type_hint,
                    "client_id": profile.client_id,
                }
            ),
            resource=profile.resource,
            requester=requester,
        )
        if status >= 400:
            all_revoked = False
            if last_status < 400:
                last_status, last_data = status, data
    return all_revoked, last_status, last_data


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _request_endpoint(
    *,
    endpoint: str,
    method: str,
    payload: dict | None,
    resource: str | None,
    requester: Requester,
) -> tuple[int, object]:
    endpoint = validate_transport_url(endpoint)
    parsed = urlsplit(endpoint)
    base_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return requester(
        CliConfig(
            base_url=base_url,
            resource=resource,
            oauth_endpoint=endpoint,
        ),
        method,
        path,
        payload,
        None,
    )


def _first_metadata_response(
    *,
    endpoints: list[str],
    label: str,
    resource: str,
    requester: Requester,
) -> tuple[int, object]:
    last_status = 404
    last_data: object = {"detail": "not found"}
    for endpoint in dict.fromkeys(endpoints):
        status, data = _request_endpoint(
            endpoint=endpoint,
            method="GET",
            payload=None,
            resource=resource,
            requester=requester,
        )
        if status < 400:
            return status, data
        last_status, last_data = status, data
        if status not in {404, 405}:
            break
    return last_status, last_data


def _required_metadata_url(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"OAuth metadata omitted {key}")
    return value


def _required_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"response omitted {key}")
    return value


def _token_response_values(
    token_data: dict,
    *,
    now: float | None,
    previous_refresh_token: str | None,
) -> tuple[str, str | None, float, str | None]:
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("token endpoint did not return an access token")
    token_type = token_data.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ValueError("token endpoint returned an unsupported token type")
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
        raise ValueError("token endpoint did not return a numeric expires_in")
    if expires_in <= 0:
        raise ValueError("token endpoint returned a non-positive expires_in")
    refresh_value = token_data.get("refresh_token")
    refresh_token = (
        refresh_value
        if isinstance(refresh_value, str) and refresh_value
        else previous_refresh_token
    )
    scope_value = token_data.get("scope")
    scope = scope_value if isinstance(scope_value, str) and scope_value else None
    current_time = time.time() if now is None else now
    return access_token, refresh_token, current_time + float(expires_in), scope


def _metadata_error(label: str, status: int, data: object) -> str:
    if isinstance(data, dict):
        detail = (
            data.get("error_description") or data.get("detail") or data.get("error")
        )
    else:
        detail = data
    return f"{label} failed ({status}): {detail or 'invalid response'}"


def _same_redirect_target(actual, expected) -> bool:
    return (
        actual.scheme.lower(),
        (actual.hostname or "").lower(),
        _effective_port(actual),
        actual.path,
    ) == (
        expected.scheme.lower(),
        (expected.hostname or "").lower(),
        _effective_port(expected),
        expected.path,
    )


def _effective_port(parsed) -> int | None:
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme.lower() == "https":
        return 443
    if parsed.scheme.lower() == "http":
        return 80
    return None


def _random_base64_url(byte_length: int) -> str:
    return secrets.token_urlsafe(byte_length)
