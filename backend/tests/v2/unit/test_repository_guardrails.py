from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from backend.tests.v2.postgres_factory import _validate_identifier


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _repository_file(name: str) -> Path:
    return REPOSITORY_ROOT / name


def test_all_ledger_compose_services_use_postgres_17() -> None:
    for name in ("compose.yaml", "compose.dev.yaml", "compose.e2e.yaml"):
        text = _repository_file(name).read_text(encoding="utf-8")
        assert "postgres:17-alpine" in text
        assert "postgres:16-alpine" not in text


def test_v2_does_not_reuse_a_postgres_16_data_volume() -> None:
    assert ".local/postgres17-data" in _repository_file("compose.yaml").read_text(encoding="utf-8")
    assert "track-anywhere-v2-postgres17" in _repository_file("compose.dev.yaml").read_text(encoding="utf-8")
    assert "postgres17-data" in _repository_file("compose.e2e.yaml").read_text(encoding="utf-8")


def test_backend_agent_rules_name_v2_units_and_postgres_17() -> None:
    text = _repository_file("backend/AGENTS.md").read_text(encoding="utf-8")
    assert "/api/v2" in text
    assert "integer units" in text
    assert "PostgreSQL 17" in text


def test_package_import_has_no_v1_service_side_effect() -> None:
    import track_anywhere

    assert not hasattr(track_anywhere, "FinanceService")


def test_python_support_range_is_finite_and_ci_testable() -> None:
    text = _repository_file("pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12,<3.14"' in text


def test_pytest_is_in_uv_default_dev_group() -> None:
    config = tomllib.loads(_repository_file("pyproject.toml").read_text(encoding="utf-8"))
    assert "pytest>=8.4" in config["dependency-groups"]["dev"]
    assert "dev" not in config.get("project", {}).get("optional-dependencies", {})


def test_external_pg17_lane_removes_inherited_sqlite_state_before_collection() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE": "1",
            "TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL": (
                "postgresql+psycopg://admin:admin@127.0.0.1:15543/postgres"
            ),
            "TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL": (
                "postgresql+psycopg://migrator:migrator@127.0.0.1:15543/postgres"
            ),
            "TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL": (
                "postgresql+psycopg://runtime:runtime@127.0.0.1:15543/postgres"
            ),
            "TRACK_ANYWHERE_DATABASE_URL": "sqlite:///:memory:",
            "TRACK_ANYWHERE_TEST_POSTGRES_URL": "sqlite:///:memory:",
            "TRACK_ANYWHERE_FAST_TEST_SCHEMA": "1",
        }
    )
    script = """
import json
import os
import runpy
import sys
from pathlib import Path

repository = Path(sys.argv[1])
runpy.run_path(str(repository / "conftest.py"))
runpy.run_path(str(repository / "backend/tests/conftest.py"))
keys = (
    "TRACK_ANYWHERE_DATABASE_URL",
    "TRACK_ANYWHERE_TEST_POSTGRES_URL",
    "TRACK_ANYWHERE_FAST_TEST_SCHEMA",
)
print(json.dumps({key: os.environ.get(key) for key in keys}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script, str(REPOSITORY_ROOT)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "TRACK_ANYWHERE_DATABASE_URL": None,
        "TRACK_ANYWHERE_TEST_POSTGRES_URL": None,
        "TRACK_ANYWHERE_FAST_TEST_SCHEMA": None,
    }


def test_compose_provisions_distinct_migrator_and_runtime_roles() -> None:
    init = _repository_file("docker/postgres/init/001-v2-roles.sh").read_text(encoding="utf-8")
    assert "TRACK_ANYWHERE_OWNER_ROLE" in init
    assert "TRACK_ANYWHERE_MIGRATOR_ROLE" in init
    assert "TRACK_ANYWHERE_RUNTIME_ROLE" in init
    assert "NOSUPERUSER" in init
    assert "NOINHERIT" in init
    assert "--set migrator_password" not in init
    assert "--set runtime_password" not in init
    assert r"\getenv migrator_password TRACK_ANYWHERE_MIGRATOR_PASSWORD" in init
    assert r"\getenv runtime_password TRACK_ANYWHERE_RUNTIME_PASSWORD" in init
    assert "pg_auth_members" in init
    assert "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE" in init


def test_postgres_identifier_validation_enforces_the_63_byte_boundary() -> None:
    accepted = "a" * 63
    rejected = "b" * 64

    assert _validate_identifier(accepted, label="test identifier") == accepted
    with pytest.raises(ValueError, match="63-byte") as error:
        _validate_identifier(rejected, label="test identifier")

    assert rejected not in str(error.value)


def test_postgres_init_statically_guards_identifier_byte_limits() -> None:
    init = _repository_file("docker/postgres/init/001-v2-roles.sh").read_text(encoding="utf-8")

    assert "export LC_ALL=C" in init
    assert "(( ${#role} > 63 ))" in init
    assert "(( ${#POSTGRES_DB} > 63 ))" in init


@pytest.mark.parametrize(
    "overlong_environment_name",
    (
        "TRACK_ANYWHERE_OWNER_ROLE",
        "TRACK_ANYWHERE_MIGRATOR_ROLE",
        "TRACK_ANYWHERE_RUNTIME_ROLE",
        "POSTGRES_DB",
    ),
)
def test_postgres_init_rejects_overlong_identifiers_before_psql(
    tmp_path: Path,
    overlong_environment_name: str,
) -> None:
    fake_psql = tmp_path / "psql"
    fake_psql.write_text("#!/bin/sh\necho psql-invoked >&2\nexit 99\n", encoding="utf-8")
    fake_psql.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{tmp_path}:{environment['PATH']}",
            "POSTGRES_USER": "track_anywhere",
            "POSTGRES_DB": "track_anywhere",
            "TRACK_ANYWHERE_OWNER_ROLE": "track_anywhere_owner",
            "TRACK_ANYWHERE_MIGRATOR_ROLE": "track_anywhere_migrator",
            "TRACK_ANYWHERE_MIGRATOR_PASSWORD": "migrator-test-password",
            "TRACK_ANYWHERE_RUNTIME_ROLE": "track_anywhere_runtime",
            "TRACK_ANYWHERE_RUNTIME_PASSWORD": "runtime-test-password",
            overlong_environment_name: "a" * 64,
        }
    )

    result = subprocess.run(
        ["bash", str(_repository_file("docker/postgres/init/001-v2-roles.sh"))],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 2
    assert "63-byte" in result.stderr
    assert environment[overlong_environment_name] not in result.stderr
    assert "psql-invoked" not in result.stderr
