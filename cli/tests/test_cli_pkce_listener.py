from __future__ import annotations

import json
import urllib.request

import track_anywhere_cli.browser_login as browser_login
import track_anywhere_cli.main as cli_main
import track_anywhere_cli.oauth_login as oauth_login
from track_anywhere_cli.main import main
from track_anywhere_cli.pkce_callback import BrowserCallbackListener


def test_browser_callback_listener_captures_local_callback():
    with BrowserCallbackListener() as listener:
        callback_url = f"{listener.redirect_uri}?code=code_cli&state=state_cli"
        with urllib.request.urlopen(callback_url, timeout=5) as response:
            body = response.read().decode("utf-8")

        assert response.status == 200
        assert "Authorized" in body
        assert listener.wait_for_callback(timeout_seconds=1) == callback_url


def test_auth_login_auto_listens_for_pkce_callback(monkeypatch, capsys):
    saved = {}
    state = "state_value_123456789012345678901234567890123456"
    generated = iter(
        [state, "verifier_value_12345678901234567890123456789012345678901234567890"]
    )

    class FakeListener:
        redirect_uri = "http://127.0.0.1:65123/callback"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

        def wait_for_callback(self):
            return f"{self.redirect_uri}?code=code_cli&state={state}"

    def fake_request(config, method, path, payload=None, key=None):
        assert config.base_url == "http://api.test"
        assert method == "POST"
        assert path == "/api/v2/oauth/token"
        assert payload["code"] == "code_cli"
        assert payload["redirect_uri"] == FakeListener.redirect_uri
        return 200, {"access_token": "ta_cli_access", "scope": "account:read"}

    monkeypatch.setattr(browser_login, "BrowserCallbackListener", FakeListener)
    monkeypatch.setattr(
        oauth_login.secrets, "token_urlsafe", lambda _length: next(generated)
    )
    monkeypatch.setattr(cli_main, "request_json", fake_request)
    monkeypatch.setattr(
        cli_main.TokenStore,
        "save",
        lambda self, token: saved.setdefault("token", token),
    )

    assert (
        main(
            ["--base-url", "http://api.test", "auth", "login", "--no-browser", "--json"]
        )
        == 0
    )

    assert saved["token"] == "ta_cli_access"
    output = capsys.readouterr()
    assert (
        "Waiting for browser callback on http://127.0.0.1:65123/callback" in output.err
    )
    payload = json.loads(output.out)
    assert payload["command"] == "auth.login"
    assert payload["data"]["token_saved"] is True
