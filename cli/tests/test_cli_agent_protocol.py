from __future__ import annotations

import json

from track_anywhere_cli.main import EXIT_VALIDATION, main


def test_json_parse_errors_emit_structured_stderr(capsys):
    exit_code = main(["--json", "nope"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["command"] == "cli.parse"
    assert payload["error"]["code"] == "unknown_command"


def test_agent_mode_requires_explicit_idempotency_key_for_mutation(capsys):
    exit_code = main(["--token", "token-1", "--agent", "account", "create", "Agent Cash"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "idempotency_key_required"


def test_agent_login_does_not_prompt_without_token_or_callback(capsys):
    exit_code = main(["--agent", "auth", "login"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "missing_required_input"


def test_schema_command_describes_command_protocol(capsys):
    assert main(["schema", "tx.record", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert command["command"] == ["ta", "tx", "record"]
    assert command["side_effects"] == ["mutates:tx.record"]
    assert any("--idempotency-key" in flag["opts"] for flag in command["flags"])


def test_capabilities_command_exposes_agent_support(capsys):
    assert main(["capabilities", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["supports"]["agent_mode"] is True
    assert payload["data"]["supports"]["agent_requires_explicit_idempotency_key"] is True
