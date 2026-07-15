from __future__ import annotations

import json
from urllib.parse import parse_qs, parse_qsl, urlparse

import pytest

import track_anywhere_cli.oauth_login as oauth_login
from track_anywhere_cli.config import CliConfig
from track_anywhere_cli.http import request_json


class _JsonResponse:
    status = 200

    def __init__(self, body: bytes = b'{"ok": true}') -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return None

    def read(self) -> bytes:
        return self._body


def _metadata():
    metadata_type = getattr(oauth_login, "OAuthMetadata")
    return metadata_type(
        resource="https://ledger.example/api/v2",
        issuer="https://auth.example",
        authorization_endpoint="https://app.example/auth/authorize",
        token_endpoint="https://auth.example/api/v2/oauth/token",
        device_authorization_endpoint="https://auth.example/api/v2/oauth/device/authorize",
        registration_endpoint="https://auth.example/api/v2/oauth/register",
        revocation_endpoint="https://auth.example/api/v2/oauth/revoke",
    )


def test_discovery_resolves_resource_and_authorization_server_metadata():
    calls = []

    def requester(config, method, path, payload=None, key=None):
        calls.append((config.base_url, method, path, payload, key))
        if path == "/.well-known/oauth-protected-resource/api/v2":
            return 200, {
                "resource": "https://ledger.example/api/v2",
                "authorization_servers": ["https://auth.example"],
                "bearer_methods_supported": ["header"],
                "scopes_supported": ["book:read", "ledger:read"],
            }
        if path == "/.well-known/oauth-authorization-server":
            return 200, {
                "issuer": "https://auth.example",
                "authorization_endpoint": "https://app.example/auth/authorize",
                "token_endpoint": "https://auth.example/api/v2/oauth/token",
                "device_authorization_endpoint": "https://auth.example/api/v2/oauth/device/authorize",
                "registration_endpoint": "https://auth.example/api/v2/oauth/register",
                "revocation_endpoint": "https://auth.example/api/v2/oauth/revoke",
                "response_types_supported": ["code"],
                "grant_types_supported": [
                    "authorization_code",
                    "refresh_token",
                    "urn:ietf:params:oauth:grant-type:device_code",
                ],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        raise AssertionError(path)

    metadata = oauth_login.discover_oauth_metadata(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
        requester=requester,
    )

    assert metadata == _metadata()
    assert calls == [
        (
            "https://ledger.example",
            "GET",
            "/.well-known/oauth-protected-resource/api/v2",
            None,
            None,
        ),
        (
            "https://auth.example",
            "GET",
            "/.well-known/oauth-authorization-server",
            None,
            None,
        ),
    ]


def test_metadata_transport_supports_custom_resource_and_advertised_endpoints(
    monkeypatch,
):
    responses = {
        "https://ledger.example/.well-known/oauth-protected-resource/custom": {
            "resource": "https://ledger.example/custom",
            "authorization_servers": ["https://auth.example/tenant"],
        },
        "https://auth.example/.well-known/oauth-authorization-server/tenant": {
            "issuer": "https://auth.example/tenant",
            "authorization_endpoint": "https://app.example/tenant/authorize",
            "token_endpoint": "https://auth.example/tenant/oauth/token",
            "device_authorization_endpoint": "https://auth.example/tenant/oauth/device",
            "registration_endpoint": "https://auth.example/tenant/oauth/register",
            "revocation_endpoint": "https://auth.example/tenant/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": [
                "authorization_code",
                "refresh_token",
                oauth_login.DEVICE_GRANT_TYPE,
            ],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        },
    }
    requested = []

    def fake_open(request, timeout):
        requested.append(request.full_url)
        body = responses.get(request.full_url, {"access_token": "custom-access"})
        return _JsonResponse(json.dumps(body).encode())

    monkeypatch.setattr("track_anywhere_cli.http._open_request", fake_open)
    metadata = oauth_login.discover_oauth_metadata(
        base_url="https://ledger.example",
        resource="https://ledger.example/custom",
        requester=request_json,
    )
    status, payload = oauth_login._request_endpoint(
        endpoint=metadata.token_endpoint,
        method="POST",
        payload=oauth_login.OAuthForm({"grant_type": "refresh_token"}),
        resource=metadata.resource,
        requester=request_json,
    )

    assert status == 200
    assert payload == {"access_token": "custom-access"}
    assert requested == [
        "https://ledger.example/.well-known/oauth-protected-resource/custom",
        "https://auth.example/.well-known/oauth-authorization-server/tenant",
        "https://auth.example/tenant/oauth/token",
    ]


def test_discovery_rejects_resource_mismatch():
    def requester(_config, _method, path, payload=None, key=None):
        assert path == "/.well-known/oauth-protected-resource/api/v2"
        return 200, {
            "resource": "https://other.example/api/v2",
            "authorization_servers": ["https://auth.example"],
        }

    with pytest.raises(ValueError, match="resource metadata did not match"):
        oauth_login.discover_oauth_metadata(
            base_url="https://ledger.example",
            resource="https://ledger.example/api/v2",
            requester=requester,
        )


def test_discovery_falls_back_to_legacy_v2_metadata_routes():
    paths = []

    def requester(_config, _method, path, payload=None, key=None):
        paths.append(path)
        if path in {
            "/.well-known/oauth-protected-resource/api/v2",
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server",
        }:
            return 404, {"detail": "not found"}
        if path == "/api/v2/oauth/protected-resource":
            return 200, {
                "resource": "https://ledger.example/api/v2",
                "authorization_servers": ["https://auth.example"],
            }
        if path == "/api/v2/oauth/authorization-server":
            return 200, {
                "issuer": "https://auth.example",
                "authorization_endpoint": "https://app.example/auth/authorize",
                "token_endpoint": "https://auth.example/api/v2/oauth/token",
                "device_authorization_endpoint": "https://auth.example/api/v2/oauth/device/authorize",
                "registration_endpoint": "https://auth.example/api/v2/oauth/register",
                "revocation_endpoint": "https://auth.example/api/v2/oauth/revoke",
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        raise AssertionError(path)

    metadata = oauth_login.discover_oauth_metadata(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
        requester=requester,
    )

    assert metadata.issuer == "https://auth.example"
    assert paths == [
        "/.well-known/oauth-protected-resource/api/v2",
        "/.well-known/oauth-protected-resource",
        "/api/v2/oauth/protected-resource",
        "/.well-known/oauth-authorization-server",
        "/api/v2/oauth/authorization-server",
    ]


def test_http_transport_encodes_oauth_form_payload(monkeypatch):
    form_type = getattr(oauth_login, "OAuthForm")
    captured = {}

    def fake_open(request, timeout):
        captured["data"] = request.data.decode("utf-8")
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        return _JsonResponse(b'{"access_token":"access"}')

    monkeypatch.setattr("track_anywhere_cli.http._open_request", fake_open)

    status, payload = request_json(
        CliConfig(base_url="https://auth.example"),
        "POST",
        "/api/v2/oauth/token",
        form_type(
            {
                "grant_type": "refresh_token",
                "refresh_token": "refresh",
                "client_id": "client",
                "resource": "https://ledger.example/api/v2",
            }
        ),
    )

    assert status == 200
    assert payload["access_token"] == "access"
    assert captured["headers"]["content-type"] == "application/x-www-form-urlencoded"
    assert dict(parse_qsl(captured["data"])) == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh",
        "client_id": "client",
        "resource": "https://ledger.example/api/v2",
    }


def test_dynamic_registration_and_browser_request_bind_resource():
    calls = []

    def requester(config, method, path, payload=None, key=None):
        calls.append((config.base_url, method, path, payload, key))
        return 201, {
            "client_id": "client-dynamic",
            "client_name": "Track Anywhere CLI",
            "redirect_uris": ["http://127.0.0.1:49152/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": "book:read ledger:read",
            "token_endpoint_auth_method": "none",
        }

    client_id = oauth_login.register_public_client(
        metadata=_metadata(),
        redirect_uri="http://127.0.0.1:49152/callback",
        scope="book:read ledger:read",
        requester=requester,
    )
    request = oauth_login.create_browser_login_request(
        metadata=_metadata(),
        client_id=client_id,
        scope="book:read ledger:read",
        redirect_uri="http://127.0.0.1:49152/callback",
    )

    assert client_id == "client-dynamic"
    assert calls[0][0:3] == (
        "https://auth.example",
        "POST",
        "/api/v2/oauth/register",
    )
    assert calls[0][3] == {
        "client_name": "Track Anywhere CLI",
        "redirect_uris": ["http://127.0.0.1:49152/callback"],
        "scope": "book:read ledger:read",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    parsed = urlparse(request.auth_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://app.example/auth/authorize"
    )
    query = parse_qs(parsed.query)
    assert query["resource"] == ["https://ledger.example/api/v2"]
    assert query["redirect_uri"] == ["http://127.0.0.1:49152/callback"]
    assert query["client_id"] == ["client-dynamic"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]


def test_callback_must_match_redirect_and_reject_duplicate_security_fields():
    with pytest.raises(ValueError, match="redirect URI"):
        oauth_login.callback_code(
            "http://127.0.0.1:49153/callback?code=code&state=state",
            expected_state="state",
            expected_redirect_uri="http://127.0.0.1:49152/callback",
        )

    with pytest.raises(ValueError, match="exactly one state"):
        oauth_login.callback_code(
            "http://127.0.0.1:49152/callback?code=code&state=state&state=state",
            expected_state="state",
            expected_redirect_uri="http://127.0.0.1:49152/callback",
        )


def test_authorization_code_exchange_uses_form_and_bound_resource():
    calls = []
    request = oauth_login.create_browser_login_request(
        metadata=_metadata(),
        client_id="client-dynamic",
        scope="book:read ledger:read",
        redirect_uri="http://127.0.0.1:49152/callback",
    )

    def requester(config, method, path, payload=None, key=None):
        calls.append((config, method, path, payload, key))
        return 200, {
            "access_token": "access",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "refresh",
            "scope": "book:read ledger:read",
        }

    status, payload = oauth_login.exchange_callback_for_token(
        request=request,
        callback_value=(f"{request.redirect_uri}?code=code-one&state={request.state}"),
        metadata=_metadata(),
        requester=requester,
    )

    assert status == 200
    assert payload["refresh_token"] == "refresh"
    config, method, path, form, key = calls[0]
    assert config.base_url == "https://auth.example"
    assert method == "POST"
    assert path == "/api/v2/oauth/token"
    assert dict(form) == {
        "grant_type": "authorization_code",
        "code": "code-one",
        "client_id": "client-dynamic",
        "redirect_uri": "http://127.0.0.1:49152/callback",
        "code_verifier": request.code_verifier,
        "resource": "https://ledger.example/api/v2",
    }
    assert key is None
