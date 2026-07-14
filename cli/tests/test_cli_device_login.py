from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import main


def test_auth_login_device_non_interactive_returns_authorization_payload(
    monkeypatch, capsys
):
    def fake_request(config, method, path, payload=None, key=None):
        assert config.base_url == "http://api.test"
        assert method == "POST"
        assert path == "/api/v2/oauth/device/authorize"
        assert payload == {
            "client_id": "track-anywhere-web",
            "scope": "account:read book:read ledger:read",
        }
        return 200, {
            "device_code": "device-code-1",
            "user_code": "ABCD-EFGH",
            "verification_uri": "http://api.test/api/v2/auth/device",
            "verification_uri_complete": "http://api.test/api/v2/auth/device?user_code=ABCD-EFGH",
            "expires_in": 900,
            "interval": 5,
        }

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(["--base-url", "http://api.test", "auth", "login", "--device", "--agent"])
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "auth.login"
    assert payload["data"]["device_authorization"]["user_code"] == "ABCD-EFGH"


def test_auth_login_device_json_no_browser_returns_authorization_payload(
    monkeypatch, capsys
):
    def fake_request(config, method, path, payload=None, key=None):
        assert config.base_url == "http://api.test"
        assert method == "POST"
        assert path == "/api/v2/oauth/device/authorize"
        return 200, {
            "device_code": "device-code-2",
            "user_code": "WXYZ-1234",
            "verification_uri": "http://api.test/api/v2/auth/device",
            "verification_uri_complete": "http://api.test/api/v2/auth/device?user_code=WXYZ-1234",
            "expires_in": 900,
            "interval": 5,
        }

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert (
        main(
            [
                "--base-url",
                "http://api.test",
                "auth",
                "login",
                "--device",
                "--no-browser",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["device_authorization"]["user_code"] == "WXYZ-1234"
