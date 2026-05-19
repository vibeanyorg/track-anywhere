from __future__ import annotations

import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.main import EXIT_AUTH, main


def _diagnostic_by_code(payload: dict, code: str):
    return next((item for item in payload["diagnostics"] if item["code"] == code), None)


def test_env_token_requires_insecure_opt_in_json_diagnostic(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    exit_code = main(["capture", "spent 38", "--idempotency-key", "k", "--json"])

    assert exit_code == EXIT_AUTH
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == 401
    assert _diagnostic_by_code(payload, "auth_required") is not None
    assert "TRACK_ANYWHERE_TOKEN" in payload["data"]["detail"]


def test_env_token_warning_is_structured(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    captured = {}

    def fake_request(config, method, path, payload=None, key=None):
        captured["token"] = config.token
        return 200, {"draft": {"draft_id": "draft_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(["--insecure-automation", "capture", "spent 38", "--idempotency-key", "k", "--json"])

    assert exit_code == 0
    assert captured["token"] == "secret"
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "capture"
    warning_diagnostic = _diagnostic_by_code(payload, "insecure_env_token")
    assert warning_diagnostic is not None
    assert warning_diagnostic["level"] == "warning"


def test_env_token_warning_is_present_for_api_failure(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    def fake_request(config, method, path, payload=None, key=None):
        return 503, {"detail": "Server unavailable"}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(["--insecure-automation", "capture", "spent 38", "--idempotency-key", "k", "--json"])

    assert exit_code != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == 503
    assert _diagnostic_by_code(payload, "request_failed") is not None
    assert _diagnostic_by_code(payload, "insecure_env_token") is not None


def test_env_token_warning_is_visible_in_human_mode(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    def fake_request(config, method, path, payload=None, key=None):
        return 200, {"draft": {"draft_id": "draft_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(["--insecure-automation", "capture", "spent 38", "--idempotency-key", "k"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Using TRACK_ANYWHERE_TOKEN with --insecure-automation." in output
    assert "Capture response" in output
