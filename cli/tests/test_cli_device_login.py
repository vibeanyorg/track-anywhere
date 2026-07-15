from __future__ import annotations

import json
import sys

from track_anywhere_cli.click_app import run
from track_anywhere_cli.config import TokenStore
from track_anywhere_cli.oauth_login import DEVICE_GRANT_TYPE, OAuthForm


def _protected_metadata():
    return {
        "resource": "https://ledger.example/api/v2",
        "authorization_servers": ["https://auth.example"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["book:read", "ledger:read"],
    }


def _authorization_metadata():
    return {
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
            DEVICE_GRANT_TYPE,
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def test_auth_login_device_agent_registers_polls_and_saves_scoped_profile(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setitem(sys.modules, "keyring", None)
    profile_file = tmp_path / "auth-profiles.json"
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(profile_file))
    calls = []

    def requester(config, method, path, payload=None, key=None):
        calls.append((config.base_url, method, path, payload, key))
        if path == "/.well-known/oauth-protected-resource/api/v2":
            return 200, _protected_metadata()
        if path == "/.well-known/oauth-authorization-server":
            return 200, _authorization_metadata()
        if path == "/api/v2/oauth/register":
            assert payload["redirect_uris"] == ["http://127.0.0.1/callback"]
            assert payload["grant_types"] == [DEVICE_GRANT_TYPE, "refresh_token"]
            assert payload["response_types"] == ["code"]
            return 201, {
                "client_id": "client-device",
                "client_name": "Track Anywhere CLI",
                "redirect_uris": payload["redirect_uris"],
                "grant_types": [DEVICE_GRANT_TYPE],
                "response_types": ["code"],
                "scope": payload["scope"],
                "token_endpoint_auth_method": "none",
            }
        if path == "/api/v2/oauth/device/authorize":
            assert payload == {
                "client_id": "client-device",
                "scope": "book:read ledger:read",
                "resource": "https://ledger.example/api/v2",
            }
            return 200, {
                "device_code": "device-code-1",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://app.example/auth/device",
                "verification_uri_complete": "https://app.example/auth/device?user_code=ABCD-EFGH",
                "expires_in": 900,
                "interval": 5,
            }
        if path == "/api/v2/oauth/token":
            assert isinstance(payload, OAuthForm)
            assert dict(payload) == {
                "grant_type": DEVICE_GRANT_TYPE,
                "device_code": "device-code-1",
                "client_id": "client-device",
                "resource": "https://ledger.example/api/v2",
            }
            return 200, {
                "access_token": "access-device",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "refresh-device",
                "scope": "book:read ledger:read",
            }
        raise AssertionError(path)

    assert (
        run(
            [
                "--base-url",
                "https://ledger.example",
                "auth",
                "login",
                "--device",
                "--agent",
            ],
            requester=requester,
        )
        == 0
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["data"]["authenticated"] is True
    assert payload["data"]["auth_kind"] == "device"
    assert "https://app.example/auth/device?user_code=ABCD-EFGH" in captured.err
    assert [path for _, _, path, _, _ in calls] == [
        "/.well-known/oauth-protected-resource/api/v2",
        "/.well-known/oauth-authorization-server",
        "/api/v2/oauth/register",
        "/api/v2/oauth/device/authorize",
        "/api/v2/oauth/token",
    ]
    stored = TokenStore(
        base_url="https://ledger.example",
        resource="https://ledger.example/api/v2",
    ).load_profile_with_source()
    assert stored is not None
    assert stored.profile.access_token == "access-device"
    assert stored.profile.refresh_token == "refresh-device"
    assert stored.profile.client_id == "client-device"


def test_auth_login_device_with_explicit_client_skips_registration(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(tmp_path / "profiles.json"))

    def requester(config, method, path, payload=None, key=None):
        if path == "/.well-known/oauth-protected-resource/api/v2":
            return 200, _protected_metadata()
        if path == "/.well-known/oauth-authorization-server":
            return 200, _authorization_metadata()
        if path == "/api/v2/oauth/register":
            raise AssertionError("explicit clients must not be dynamically registered")
        if path == "/api/v2/oauth/device/authorize":
            assert payload["client_id"] == "client-explicit"
            return 200, {
                "device_code": "device-code-2",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://app.example/auth/device",
                "verification_uri_complete": "https://app.example/auth/device?user_code=WXYZ-1234",
                "expires_in": 900,
                "interval": 5,
            }
        if path == "/api/v2/oauth/token":
            return 200, {
                "access_token": "access-explicit",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "refresh-explicit",
                "scope": "book:read ledger:read",
            }
        raise AssertionError(path)

    assert (
        run(
            [
                "--base-url",
                "https://ledger.example",
                "auth",
                "login",
                "--device",
                "--client-id",
                "client-explicit",
                "--agent",
            ],
            requester=requester,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data"]["authenticated"] is True
