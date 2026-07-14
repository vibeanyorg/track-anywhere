from __future__ import annotations

import json
from pathlib import Path

import pytest

from track_anywhere_cli.click_app import cli, run
from track_anywhere_cli.commands import command_paths
from track_anywhere_cli.config import CliConfig
from track_anywhere_cli.http import request_json
from track_anywhere_cli.protocol import command_schema


def _never_request(*_args, **_kwargs):
    raise AssertionError("unsupported V2 capability must not contact the server")


@pytest.mark.parametrize(
    "argv, command_path",
    [
        (
            ["--token", "token", "payment", "profile", "list", "--json"],
            "payment.profile.list",
        ),
        (
            ["--token", "token", "recurring", "list", "--json"],
            "recurring.list",
        ),
        (["data", "backup", "--json"], "data.backup"),
        (["auth", "dev-token", "--json"], "auth.dev_token"),
    ],
)
def test_deferred_capabilities_fail_fast_locally(
    argv,
    command_path,
    capsys,
):
    assert run(argv, requester=_never_request) != 0
    payload = json.loads(capsys.readouterr().err)
    assert payload["command"] == command_path
    assert "did not" in payload["data"]["detail"]


def test_capabilities_advertise_only_v2_implemented_commands(capsys):
    assert run(["capabilities", "--json"], requester=_never_request) == 0
    payload = json.loads(capsys.readouterr().out)
    advertised = {command["command_path"] for command in payload["data"]["commands"]}

    assert advertised == set(command_paths())
    assert all(command["registered"] for command in payload["data"]["commands"])
    assert not any(path.startswith(("payment.", "recurring.")) for path in advertised)
    assert payload["data"]["api_version"] == "v2"


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

    assert "/api/v1" not in sources
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
