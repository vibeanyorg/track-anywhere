from __future__ import annotations

from argparse import Namespace
import json

import track_anywhere_cli.main as cli_main
from track_anywhere_cli.click_common import ClickState, run_api
from track_anywhere_cli.main import EXIT_AUTH, main


def _json_from_output(captured):
    return json.loads(captured.out or captured.err)


def _diagnostic_by_code(payload: dict, code: str):
    return next((item for item in payload["diagnostics"] if item["code"] == code), None)


def test_env_token_requires_insecure_opt_in_json_diagnostic(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    exit_code = main(["capture", "spent 38", "--idempotency-key", "k", "--json"])

    assert exit_code == EXIT_AUTH
    payload = _json_from_output(capsys.readouterr())
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
    payload = _json_from_output(capsys.readouterr())
    assert payload["status"] == 503
    assert _diagnostic_by_code(payload, "external_dependency_error") is not None
    assert _diagnostic_by_code(payload, "insecure_env_token") is not None


def test_env_token_warning_is_present_for_unknown_api_command(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")
    state = ClickState(
        base_url="http://api.test",
        token=None,
        insecure_automation=True,
        json_mode=True,
        no_color=True,
        requester=lambda config, method, path, payload=None, key=None: (200, {}),
    )
    args = Namespace(
        command="unknown",
        base_url=state.base_url,
        token=None,
        insecure_automation=True,
        json=True,
        no_color=True,
    )

    exit_code = run_api(args, state=state, command_path="unknown.command")

    assert exit_code != 0
    payload = _json_from_output(capsys.readouterr())
    assert payload["status"] == 400
    assert _diagnostic_by_code(payload, "insecure_env_token") is not None


def test_env_token_warning_is_visible_in_human_mode(monkeypatch, capsys):
    monkeypatch.setenv("TRACK_ANYWHERE_TOKEN", "secret")

    def fake_request(config, method, path, payload=None, key=None):
        return 200, {"draft": {"draft_id": "draft_1"}}

    monkeypatch.setattr(cli_main, "request_json", fake_request)

    exit_code = main(["--insecure-automation", "capture", "spent 38", "--idempotency-key", "k"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Using TRACK_ANYWHERE_TOKEN with --insecure-automation." in captured.err
    assert "Capture confirmed" in captured.out
    assert "draft_1" in captured.out
