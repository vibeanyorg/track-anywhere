from __future__ import annotations

import json
from argparse import Namespace

import pytest

from track_anywhere_cli.click_app import run
from track_anywhere_cli.commands import (
    command_definitions,
    command_spec,
    dispatch_api_command,
)
from track_anywhere_cli.config import CliConfig
from track_anywhere_cli.exit_codes import (
    EXIT_EXTERNAL_DEPENDENCY,
    EXIT_NOT_FOUND,
    EXIT_POLICY_DENIED,
)


BOOK = "book /?"
ARCHIVE = "archive /?"


def _recorder(calls, *, data=None):
    def request(_config, method, path, payload=None, key=None):
        calls.append((method, path, payload, key))
        return 200, data if data is not None else {"ok": True}

    return request


def test_archive_click_commands_use_explicit_owner_only_read_routes(capsys):
    calls = []
    requester = _recorder(calls)

    assert (
        run(
            ["--token", "token", "archive", "list", BOOK, "--json"],
            requester=requester,
        )
        == 0
    )
    assert (
        run(
            [
                "--token",
                "token",
                "archive",
                "export",
                BOOK,
                ARCHIVE,
                "--json",
            ],
            requester=requester,
        )
        == 0
    )
    capsys.readouterr()

    assert calls == [
        (
            "GET",
            "/api/v2/books/book%20%2F%3F/import-archives",
            None,
            None,
        ),
        (
            "GET",
            "/api/v2/books/book%20%2F%3F/import-archives/"
            "archive%20%2F%3F/export",
            None,
            None,
        ),
    ]


def test_archive_direct_dispatch_uses_exact_owner_only_read_routes():
    calls = []
    requester = _recorder(calls)
    config = CliConfig(base_url="http://testserver", token="token")

    assert dispatch_api_command(
        Namespace(
            command="archive",
            archive_command="list",
            book_id=BOOK,
        ),
        config,
        requester,
    ) == (200, {"ok": True})
    assert dispatch_api_command(
        Namespace(
            command="archive",
            archive_command="export",
            book_id=BOOK,
            archive_id=ARCHIVE,
        ),
        config,
        requester,
    ) == (200, {"ok": True})

    assert calls == [
        (
            "GET",
            "/api/v2/books/book%20%2F%3F/import-archives",
            None,
            None,
        ),
        (
            "GET",
            "/api/v2/books/book%20%2F%3F/import-archives/"
            "archive%20%2F%3F/export",
            None,
            None,
        ),
    ]


def test_archive_command_policy_comes_from_the_public_registry():
    definitions = command_definitions()

    for command_path in ("archive.list", "archive.export"):
        definition = definitions[command_path]
        assert definition.requires_auth is True
        assert definition.mutating is False
        assert definition.idempotent is False
        assert command_spec(command_path).requires_auth is True


def test_archive_export_stays_inside_structured_json_output(capsys):
    calls = []
    ndjson = '{"private":"line one"}\n{"private":"line two"}\n'

    assert (
        run(
            [
                "--token",
                "token",
                "archive",
                "export",
                BOOK,
                ARCHIVE,
                "--json",
            ],
            requester=_recorder(
                calls,
                data={"content_type": "application/x-ndjson", "ndjson": ndjson},
            ),
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["command"] == "archive.export"
    assert payload["data"]["ndjson"] == ndjson


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (403, EXIT_POLICY_DENIED),
        (404, EXIT_NOT_FOUND),
        (503, EXIT_EXTERNAL_DEPENDENCY),
    ],
)
def test_archive_runtime_preserves_error_exit_mapping(
    status,
    expected_exit,
    capsys,
):
    def requester(*_args, **_kwargs):
        return status, {"detail": f"archive failure {status}"}

    assert (
        run(
            ["--token", "token", "archive", "list", BOOK, "--json"],
            requester=requester,
        )
        == expected_exit
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["command"] == "archive.list"
    assert payload["status"] == status
