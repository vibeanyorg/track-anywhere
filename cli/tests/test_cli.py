from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

import track_anywhere_cli.main as cli_main
import track_anywhere_cli.oauth_login as oauth_login
from track_anywhere.posting_semantics import backup_posting_semantics_metadata
from track_anywhere_cli.main import EXIT_AUTH, EXIT_VALIDATION, cli, exit_for_status, main
from track_anywhere_cli.output import CliDiagnostic


def _json_from_output(captured):
    return json.loads(captured.out or captured.err)


def test_cli_rejects_env_token_without_insecure_opt_in(monkeypatch):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")
    assert main(["capture", "spent 38", "--idempotency-key", "k"]) == EXIT_AUTH


def test_click_cli_exposes_recurring_help():
    result = CliRunner().invoke(cli, ["recurring", "--help"])

    assert result.exit_code == 0
    assert "draft-due" in result.output
    assert "reminders" in result.output


def test_cli_accepts_service_url_env_alias(monkeypatch, capsys):
    monkeypatch.delenv("TRACK_ANYWHERE_API", raising=False)
    monkeypatch.setenv("TRACK_ANYWHERE_SERVICE_URL", "http://track-anywhere-prod:8000")

    def fake_request(config, method, path, payload=None, key=None):
        return 200, {"authenticated": True, "credential_id": "cred_1"}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "auth", "status", "--json"]) == 0
    payload = _json_from_output(capsys.readouterr())
    assert payload["data"]["base_url"] == "http://track-anywhere-prod:8000"


def test_exit_code_mapping_for_conflicts():
    assert exit_for_status(409, {"detail": "idempotency key reused"}) == 4
    assert exit_for_status(409, {"detail": "draft version conflict"}) == 5


def test_capture_dry_run_json_contract(capsys):
    exit_code = main(["capture", "spent 38", "--idempotency-key", "snap-1", "--dry-run", "--json"])
    expected = json.loads((Path(__file__).parent / "snapshots" / "capture-dry-run.json").read_text())

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_default_output_is_human_rendered_with_rich(monkeypatch, capsys):
    def fake_request(config, method, path, payload=None, key=None):
        return 200, {
            "reminders": [
                {
                    "name": "ChatGPT",
                    "provider": "OpenAI",
                    "renewal_date": "2026-06-15",
                    "reminder_date": "2026-06-12",
                    "lead_days": 3,
                    "amount": "20",
                    "currency": "USD",
                }
            ]
        }

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    assert main(["--token", "token-1", "recurring", "reminders", "--as-of", "2026-06-12"]) == 0
    output = capsys.readouterr().out
    assert "ChatGPT" in output
    assert "Renewal" in output
    assert not output.lstrip().startswith("{")


def test_auth_dev_token_saves_local_token(monkeypatch, capsys):
    saved = {}

    def fake_request(config, method, path, payload=None, key=None):
        assert method == "POST"
        assert path == "/api/v1/auth/dev-token"
        return 200, {"token": "owner-token", "actor": {"actor_id": "owner"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)
    monkeypatch.setattr(cli_main.TokenStore, "save", lambda self, token: saved.setdefault("token", token))

    assert main(["auth", "dev-token", "--json"]) == 0
    assert saved["token"] == "owner-token"
    payload = _json_from_output(capsys.readouterr())
    assert payload["ok"] is True
    assert payload["command"] == "auth.dev_token"
    assert payload["status"] == 200
    assert payload["data"]["token"] == "owner-token"


def test_auth_login_with_token_still_saves_token(monkeypatch, capsys):
    saved = {}
    monkeypatch.setattr(cli_main.TokenStore, "save", lambda self, token: saved.setdefault("token", token))

    assert main(["auth", "login", "ta_manual_token", "--json"]) == 0

    assert saved["token"] == "ta_manual_token"
    payload = _json_from_output(capsys.readouterr())
    assert payload["ok"] is True
    assert payload["command"] == "auth.login"
    assert payload["status"] == 200
    assert payload["data"]["token_saved"] is True


def test_auth_login_token_store_warning_is_structured_diagnostic(monkeypatch, capsys):
    saved = {}

    def save_with_warning(self, token):
        saved["token"] = token
        return [CliDiagnostic(level="warning", code="token_file_fallback", message="saved token to file")]

    monkeypatch.setattr(cli_main.TokenStore, "save", save_with_warning)

    assert main(["auth", "login", "ta_manual_token", "--json"]) == 0

    assert saved["token"] == "ta_manual_token"
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostics"][0]["code"] == "token_file_fallback"
    assert payload["diagnostics"][0]["level"] == "warning"


def test_top_level_login_uses_auth_login_output_contract(monkeypatch, capsys):
    saved = {}
    monkeypatch.setattr(cli_main.TokenStore, "save", lambda self, token: saved.setdefault("token", token))

    assert main(["login", "ta_manual_token", "--json"]) == 0

    assert saved["token"] == "ta_manual_token"
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.login"
    assert payload["status"] == 200
    assert payload["data"]["token_saved"] is True


def test_auth_login_without_token_uses_pkce_callback_exchange(monkeypatch, capsys):
    saved = {}
    generated = iter(["state_value_123456789012345678901234567890123456", "verifier_value_12345678901234567890123456789012345678901234567890"])

    def fake_request(config, method, path, payload=None, key=None):
        assert config.base_url == "http://api.test"
        assert method == "POST"
        assert path == "/api/v1/oauth/token"
        assert payload["code"] == "code_cli"
        assert payload["client_id"] == "track-anywhere-web"
        assert payload["redirect_uri"] == "http://api.test/api/v1/auth/callback"
        assert payload["code_verifier"].startswith("verifier_value")
        return 200, {"access_token": "ta_cli_access", "scope": payload.get("scope", "account:read")}

    monkeypatch.setattr(cli_main, "request_json", fake_request)
    monkeypatch.setattr(cli_main.TokenStore, "save", lambda self, token: saved.setdefault("token", token))
    monkeypatch.setattr(oauth_login.secrets, "token_urlsafe", lambda _length: next(generated))

    callback = "http://api.test/api/v1/auth/callback?code=code_cli&state=state_value_123456789012345678901234567890123456"
    assert main(["--base-url", "http://api.test", "auth", "login", "--no-browser", "--callback", callback, "--json"]) == 0

    assert saved["token"] == "ta_cli_access"
    output = capsys.readouterr()
    assert "Open this URL" not in output.err
    payload = json.loads(output.out)
    assert payload["command"] == "auth.login"
    assert payload["status"] == 200
    assert payload["data"]["token_saved"] is True
    assert payload["data"]["scope"] == "account:read"


def test_auth_login_rejects_callback_state_mismatch(monkeypatch, capsys):
    generated = iter(["state_value_123456789012345678901234567890123456", "verifier_value_12345678901234567890123456789012345678901234567890"])
    monkeypatch.setattr(oauth_login.secrets, "token_urlsafe", lambda _length: next(generated))

    callback = "http://api.test/api/v1/auth/callback?code=code_cli&state=wrong"
    assert main(["auth", "login", "--no-browser", "--callback", callback, "--json"]) == EXIT_VALIDATION

    output = capsys.readouterr()
    payload = _json_from_output(output)
    assert payload["ok"] is False
    assert payload["command"] == "auth.login"
    assert payload["status"] == 400
    assert payload["diagnostics"][0]["code"] == "security_precondition"
    assert "state did not match" in payload["diagnostics"][0]["message"]


def test_auth_login_exchange_exception_still_returns_json_envelope(monkeypatch, capsys):
    generated = iter(["state_value_123456789012345678901234567890123456", "verifier_value_12345678901234567890123456789012345678901234567890"])

    def broken_request(config, method, path, payload=None, key=None):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(cli_main, "request_json", broken_request)
    monkeypatch.setattr(oauth_login.secrets, "token_urlsafe", lambda _length: next(generated))

    callback = "http://api.test/api/v1/auth/callback?code=code_cli&state=state_value_123456789012345678901234567890123456"
    assert main(["auth", "login", "--no-browser", "--callback", callback, "--json"]) == cli_main.EXIT_EXTERNAL_DEPENDENCY

    output = capsys.readouterr()
    payload = _json_from_output(output)
    assert payload["ok"] is False
    assert payload["command"] == "auth.login"
    assert payload["status"] == 500
    assert payload["diagnostics"][0]["code"] == "external_dependency_error"
    assert "network unavailable" in payload["diagnostics"][0]["message"]


def test_data_backup_creates_readable_sqlite_copy(tmp_path, capsys):
    database_path = tmp_path / "track-anywhere.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("create table accounts (account_id text primary key, name text)")
        connection.execute("insert into accounts values ('acc_1', 'Cash')")

    exit_code = main(
        [
            "data",
            "backup",
            "--database-url",
            f"sqlite:///{database_path}",
            "--output-dir",
            str(tmp_path / "backups"),
            "--label",
            "before-real-write",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = _json_from_output(capsys.readouterr())
    assert payload["ok"] is True
    assert payload["command"] == "data.backup"
    assert payload["status"] == 200
    backup_path = Path(payload["data"]["backup"]["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == tmp_path / "backups"
    assert "before-real-write" in backup_path.name
    assert payload["data"]["backup"]["posting_semantics"] == backup_posting_semantics_metadata()
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("select name from accounts where account_id = 'acc_1'").fetchone() == ("Cash",)


def test_data_backup_missing_database_preserves_validation_exit_code(tmp_path, capsys):
    missing_database = tmp_path / "missing.sqlite3"

    exit_code = main(
        [
            "data",
            "backup",
            "--database-url",
            f"sqlite:///{missing_database}",
            "--output-dir",
            str(tmp_path / "backups"),
            "--json",
        ]
    )

    assert exit_code == EXIT_VALIDATION
    payload = _json_from_output(capsys.readouterr())
    assert payload["ok"] is False
    assert payload["command"] == "data.backup"
    assert payload["status"] == 400
    assert "sqlite database not found" in payload["diagnostics"][0]["message"]
