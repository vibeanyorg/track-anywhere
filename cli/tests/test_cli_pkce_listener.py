from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import track_anywhere_cli.browser_login as browser_login
import track_anywhere_cli.main as cli_main
from track_anywhere_cli.browser_login import capture_browser_callback
from track_anywhere_cli.main import main
from track_anywhere_cli.oauth_login import DEVICE_GRANT_TYPE, OAuthMetadata, OAuthForm
from track_anywhere_cli.pkce_callback import (
    BrowserCallbackListener,
    CallbackTimeout,
)


def test_browser_callback_listener_captures_local_callback():
    with BrowserCallbackListener() as listener:
        callback_url = f"{listener.redirect_uri}?code=code_cli&state=state_cli"
        with urllib.request.urlopen(callback_url, timeout=5) as response:
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "Authorized" in body
        assert listener.wait_for_callback(timeout_seconds=1) == callback_url


def test_browser_callback_listener_ignores_wrong_state_before_valid_callback():
    with BrowserCallbackListener() as listener:
        listener.expect_state("expected-state")
        wrong = f"{listener.redirect_uri}?code=wrong&state=attacker-state"
        try:
            urllib.request.urlopen(wrong, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("wrong-state callbacks must be rejected")

        valid = f"{listener.redirect_uri}?code=right&state=expected-state"
        with urllib.request.urlopen(valid, timeout=5) as response:
            assert response.status == 200

        assert listener.wait_for_callback(timeout_seconds=1) == valid


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


def _metadata():
    return OAuthMetadata(
        resource="https://ledger.example/api/v2",
        issuer="https://auth.example",
        authorization_endpoint="https://app.example/auth/authorize",
        token_endpoint="https://auth.example/api/v2/oauth/token",
        device_authorization_endpoint="https://auth.example/api/v2/oauth/device/authorize",
        registration_endpoint="https://auth.example/api/v2/oauth/register",
        revocation_endpoint="https://auth.example/api/v2/oauth/revoke",
    )


def test_auth_login_auto_listens_for_pkce_callback(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(tmp_path / "profiles.json"))

    class FakeListener:
        redirect_uri = "http://127.0.0.1:65123/callback"

        def __init__(self):
            self.expected_state = None

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

        def expect_state(self, state):
            self.expected_state = state

        def wait_for_callback(self):
            return f"{self.redirect_uri}?code=code_cli&state={self.expected_state}"

    def fake_request(config, method, path, payload=None, key=None):
        if path == "/.well-known/oauth-protected-resource/api/v2":
            return 200, _protected_metadata()
        if path == "/.well-known/oauth-authorization-server":
            return 200, _authorization_metadata()
        if path == "/api/v2/oauth/register":
            assert payload["redirect_uris"] == [FakeListener.redirect_uri]
            assert payload["grant_types"] == ["authorization_code", "refresh_token"]
            assert payload["response_types"] == ["code"]
            return 201, {
                "client_id": "client-browser",
                "client_name": "Track Anywhere CLI",
                "redirect_uris": payload["redirect_uris"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": payload["scope"],
                "token_endpoint_auth_method": "none",
            }
        if path == "/api/v2/oauth/token":
            assert config.base_url == "https://auth.example"
            assert method == "POST"
            assert isinstance(payload, OAuthForm)
            assert payload["code"] == "code_cli"
            assert payload["redirect_uri"] == FakeListener.redirect_uri
            assert payload["resource"] == "https://ledger.example/api/v2"
            return 200, {
                "access_token": "ta_cli_access",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "ta_cli_refresh",
                "scope": "book:read ledger:read",
            }
        raise AssertionError(path)

    monkeypatch.setattr(browser_login, "BrowserCallbackListener", FakeListener)
    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--base-url",
                "https://ledger.example",
                "auth",
                "login",
                "--no-browser",
                "--json",
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert (
        "Waiting for browser callback on http://127.0.0.1:65123/callback" in output.err
    )
    payload = json.loads(output.out)
    assert payload["command"] == "auth.login"
    assert payload["data"]["token_saved"] is True


def test_browser_callback_timeout_does_not_create_a_second_pkce_attempt(monkeypatch):
    registrations = []

    class TimeoutListener:
        redirect_uri = "http://127.0.0.1:65124/callback"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

        def expect_state(self, _state):
            return None

        def wait_for_callback(self):
            raise CallbackTimeout("timeout")

    class Interaction:
        def open_url(self, _url):
            return None

        def prompt(self, _text, *, secret=False):
            raise AssertionError(
                "browser timeout must not fall back to pasted callbacks"
            )

        def inform(self, _text):
            return None

    def requester(_config, _method, path, payload=None, key=None):
        assert path == "/api/v2/oauth/register"
        registrations.append(payload)
        return 201, {
            "client_id": "client-timeout",
            "client_name": "Track Anywhere CLI",
            "redirect_uris": payload["redirect_uris"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": payload["scope"],
            "token_endpoint_auth_method": "none",
        }

    monkeypatch.setattr(browser_login, "BrowserCallbackListener", TimeoutListener)

    with pytest.raises(CallbackTimeout):
        capture_browser_callback(
            metadata=_metadata(),
            client_id=None,
            scope="book:read ledger:read",
            interaction=Interaction(),
            requester=requester,
        )

    assert len(registrations) == 1
