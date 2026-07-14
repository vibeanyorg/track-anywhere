from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[4]
WRAPPER = ROOT / "scripts" / "pg17-client.sh"
COMPOSE = ROOT / "compose.e2e.yaml"


def _fake_docker(tmp_path: Path) -> Path:
    binary = tmp_path / "bin" / "docker"
    binary.parent.mkdir()
    binary.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
payload="$(cat)"
printf 'docker-argv=%s\\n' "$*"
printf 'stdin=%s\\n' "$payload"
if [[ "$*" == *"--version"* ]]; then
  printf '%s (PostgreSQL) 17.6\\n' "${FAKE_CLIENT_NAME:-client}"
fi
exit "${FAKE_DOCKER_EXIT:-0}"
""",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary.parent


def _run_wrapper(
    tmp_path: Path,
    *arguments: str,
    stdin: str = "",
    exit_code: int = 0,
) -> subprocess.CompletedProcess[str]:
    fake_bin = _fake_docker(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["FAKE_DOCKER_EXIT"] = str(exit_code)
    environment["FAKE_CLIENT_NAME"] = arguments[0] if arguments else "client"
    return subprocess.run(
        [str(WRAPPER), *arguments],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def test_compose_declares_an_isolated_pinned_pg17_client_service() -> None:
    content = COMPOSE.read_text(encoding="utf-8")
    client_block = content.split("  pg17-client:\n", maxsplit=1)[1].split(
        "\n  api:\n", maxsplit=1
    )[0]

    assert "image: postgres:17-alpine" in client_block
    assert 'profiles: ["tools"]' in client_block
    assert "ports:" not in client_block
    assert "volumes:" not in client_block
    assert "network_mode:" not in client_block


@pytest.mark.parametrize("client", ("psql", "pg_restore", "pg_dump"))
def test_wrapper_runs_only_the_allowlisted_pg17_compose_client(
    tmp_path: Path, client: str
) -> None:
    result = _run_wrapper(tmp_path, client, "--version")

    assert result.returncode == 0
    assert f"{client} (PostgreSQL) 17.6" in result.stdout
    assert "-p track-anywhere-v2-test" in result.stdout
    assert f"run --rm -T --no-deps pg17-client {client} --version" in result.stdout
    assert "postgresql+psycopg" not in result.stdout


def test_wrapper_preserves_stdin_and_client_exit_status(tmp_path: Path) -> None:
    result = _run_wrapper(
        tmp_path,
        "pg_restore",
        "--dbname",
        "redacted",
        stdin="custom-dump-bytes",
        exit_code=23,
    )

    assert result.returncode == 23
    assert "stdin=custom-dump-bytes" in result.stdout


@pytest.mark.parametrize("arguments", ((), ("postgres",), ("sh",)))
def test_wrapper_rejects_missing_or_unapproved_commands(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    result = _run_wrapper(tmp_path, *arguments)

    assert result.returncode == 2
    assert "docker-argv=" not in result.stdout
