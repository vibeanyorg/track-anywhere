from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_docker_postgres_e2e_fails_fast_when_docker_cli_hangs():
    source = (REPO_ROOT / "scripts/e2e-docker-postgres.sh").read_text()

    assert "DOCKER_CLI_TIMEOUT_SECONDS" in source
    assert "DOCKER_COMPOSE_TIMEOUT_SECONDS" in source
    assert "def test_docker_postgres_e2e_fails_fast" not in source
    assert "subprocess.run(command, timeout=timeout_seconds)" in source
    assert 'run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" docker version' in source
    assert 'run_with_timeout "$DOCKER_COMPOSE_TIMEOUT_SECONDS" "${COMPOSE[@]}" up' in source
    assert 'run_with_timeout "$DOCKER_CLI_TIMEOUT_SECONDS" "${COMPOSE[@]}" down' in source
