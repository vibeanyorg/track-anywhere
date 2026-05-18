from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import EXIT_AUTH, cli, exit_for_status, main


def test_cli_rejects_env_token_without_insecure_opt_in(monkeypatch):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")
    assert main(["capture", "spent 38", "--idempotency-key", "k"]) == EXIT_AUTH


def test_click_cli_exposes_recurring_help():
    result = CliRunner().invoke(cli, ["recurring", "--help"])

    assert result.exit_code == 0
    assert "draft-due" in result.output
    assert "reminders" in result.output


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
    assert json.loads(capsys.readouterr().out)["token"] == "owner-token"


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
    payload = json.loads(capsys.readouterr().out)
    backup_path = Path(payload["backup"]["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == tmp_path / "backups"
    assert "before-real-write" in backup_path.name
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("select name from accounts where account_id = 'acc_1'").fetchone() == ("Cash",)
