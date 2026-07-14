from __future__ import annotations

import json
import sys
from pathlib import Path

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.config import TokenStore
from track_anywhere_cli.main import EXIT_AUTH, main


def _json_from_output(captured):
    return json.loads(captured.out or captured.err)


def test_token_store_keyring_write_failure_falls_back_to_structured_warning(
    monkeypatch, tmp_path
):
    class FailingKeyring:
        @staticmethod
        def set_password(*args, **kwargs):
            raise RuntimeError("keyring unavailable")

    monkeypatch.setitem(sys.modules, "keyring", FailingKeyring)
    monkeypatch.delenv("TRACK_ANYWHERE_TOKEN_FILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    token_file = tmp_path / ".config" / "track-anywhere" / "token"

    diagnostics = cli_main.TokenStore().save("ta_manual_token")

    assert token_file.read_text(encoding="utf-8") == "ta_manual_token\n"
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert diagnostics[0].code == "token_file_fallback"
    assert diagnostics[0].level == "warning"


def test_explicit_token_file_takes_precedence_over_keyring(monkeypatch, tmp_path):
    class Keyring:
        @staticmethod
        def get_password(*args, **kwargs):
            return "ta_keyring_token"

        @staticmethod
        def set_password(*args, **kwargs):
            raise AssertionError("explicit token files should not write to keyring")

    token_file = tmp_path / "stable-token"
    token_file.write_text("ta_file_token\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "keyring", Keyring)
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN_FILE", str(token_file))

    store = TokenStore()
    assert store.load() == "ta_file_token"
    assert store.load_with_source().source == "token_file"

    diagnostics = store.save("ta_new_file_token")
    assert diagnostics == []
    assert token_file.read_text(encoding="utf-8") == "ta_new_file_token\n"


def test_auth_status_fails_when_server_rejects_stored_token(monkeypatch, capsys):
    def fake_request(config, method, path, payload=None, key=None):
        assert path == "/api/v2/auth/token-status"
        return 401, {"detail": "credential is missing, expired, or revoked"}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "ta_expired", "auth", "status", "--json"]) == EXIT_AUTH
    payload = _json_from_output(capsys.readouterr())
    assert payload["ok"] is False
    assert payload["status"] == 401
    assert payload["data"]["authenticated"] is False
    assert payload["data"]["token_source"] == "configured"
    assert payload["diagnostics"][0]["code"] == "auth_required"
