from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from track_anywhere_cli.click_app import cli, run
from track_anywhere_cli import commands as cli_commands
from track_anywhere_cli.commands import command_paths, command_spec
from track_anywhere_cli.config import CliConfig
from track_anywhere_cli.http import request_json
from track_anywhere_cli.protocol import command_schema


def _never_request(*_args, **_kwargs):
    raise AssertionError("unsupported V2 capability must not contact the server")


@pytest.mark.parametrize(
    "argv",
    [
        ["--token", "token", "payment", "profile", "list", "--json"],
        ["--token", "token", "recurring", "list", "--json"],
        ["data", "backup", "--json"],
        ["auth", "dev-token", "--json"],
    ],
)
def test_removed_commands_are_absent_and_fail_before_network(argv, capsys):
    assert run(argv, requester=_never_request) != 0
    payload = json.loads(capsys.readouterr().err)
    assert payload["command"] == "cli.parse"
    assert payload["data"]["error"]["code"] == "unknown_command"


def test_root_help_exposes_only_current_v2_groups(capsys):
    assert run(["--help"], requester=_never_request) == 0
    help_text = capsys.readouterr().out

    assert "\n  payment " not in help_text
    assert "\n  recurring " not in help_text
    assert "\n  data " not in help_text


def test_capabilities_advertise_only_v2_implemented_commands(capsys):
    assert run(["capabilities", "--json"], requester=_never_request) == 0
    payload = json.loads(capsys.readouterr().out)
    advertised = {command["command_path"] for command in payload["data"]["commands"]}

    assert advertised == set(command_paths())
    assert all(command["registered"] for command in payload["data"]["commands"])
    assert not any(path.startswith(("payment.", "recurring.")) for path in advertised)
    assert payload["data"]["api_version"] == "v2"
    assert payload["data"]["schema_version"] == "2026-07-15"


def test_command_definitions_are_the_single_source_for_paths_and_policy():
    assert hasattr(cli_commands, "command_definitions"), (
        "CLI command paths and policy need one definition registry"
    )
    definitions = cli_commands.command_definitions()

    assert sorted(definitions) == command_paths()
    assert definitions["system.health"].requires_auth is False
    assert definitions["account.list"].mutating is False
    assert definitions["account.create"].mutating is True
    assert definitions["account.create"].idempotent is False
    assert definitions["tx.record"].mutating is True
    assert definitions["tx.record"].idempotent is True
    assert definitions["release.bump"].local is True

    for command_path, definition in definitions.items():
        if definition.local:
            continue
        assert command_spec(command_path).requires_auth is definition.requires_auth


def test_command_path_inference_uses_the_definition_registry(monkeypatch):
    existing_definition = cli_commands.API_COMMAND_HANDLERS["system.health"]
    monkeypatch.setattr(
        cli_commands,
        "API_COMMAND_HANDLERS",
        {"future.do_work": existing_definition},
    )

    assert (
        cli_commands.infer_command_path(
            Namespace(command="future", future_command="do-work")
        )
        == "future.do_work"
    )
    assert (
        cli_commands.infer_command_path(
            Namespace(command="future", future_command="not-registered")
        )
        is None
    )


def test_v2_schema_describes_exact_string_amount_transport():
    schema = command_schema(cli, "tx.record")
    posting = next(flag for flag in schema["flags"] if flag["name"] == "posting")

    assert schema["idempotent"] is True
    assert posting["type"] == "text"
    assert posting["multiple"] is True


def test_cli_runtime_contains_no_v1_or_legacy_posting_adapter():
    runtime = Path("cli/track_anywhere_cli")
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime.rglob("*.py")
    )

    assert "/api/" + "v1" not in sources
    assert "posting_semantics" not in sources
    assert not (runtime / "posting_semantics.py").exists()


def test_http_transport_rejects_non_v2_routes_before_network(monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("a non-V2 route must never reach urllib")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    status, payload = request_json(
        CliConfig(base_url="http://api.test", token="token"),
        "GET",
        "/unsupported/route",
    )

    assert status == 400
    assert payload["error"]["code"] == "unsupported_api_route"


def test_system_checks_use_public_v2_routes_without_token(capsys):
    calls = []

    def request(config, method, path, payload=None, key=None):
        calls.append((config.token, method, path, payload, key))
        return 200, {"ok": True}

    assert run(["system", "health", "--json"], requester=request) == 0
    assert run(["system", "ready", "--json"], requester=request) == 0
    capsys.readouterr()

    assert calls == [
        (None, "GET", "/api/v2/health", None, None),
        (None, "GET", "/api/v2/ready", None, None),
    ]
