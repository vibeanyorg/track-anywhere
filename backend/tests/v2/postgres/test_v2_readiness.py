from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

from track_anywhere.api.v2 import system as system_module
from track_anywhere.api.v2.system import create_system_router


class _DatabaseMustNotBeUsed:
    def connect(self):
        raise AssertionError("health must not access the database")


class _VersionResult:
    def __init__(self, version: int) -> None:
        self._version = version

    def scalar_one(self) -> int:
        return self._version


class _VersionConnection:
    def __init__(self, version: int) -> None:
        self._version = version

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def exec_driver_sql(self, statement: str) -> _VersionResult:
        assert statement == "SHOW server_version_num"
        return _VersionResult(self._version)


class _VersionEngine:
    url = SimpleNamespace(drivername="postgresql+psycopg")

    def __init__(self, version: int) -> None:
        self._version = version

    def connect(self) -> _VersionConnection:
        return _VersionConnection(self._version)


def _client(engine, expected_runtime_role: str) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_system_router(
            engine=engine,
            expected_runtime_role=expected_runtime_role,
        ),
        prefix="/api/v2",
    )
    return TestClient(app)


def _assert_redacted_not_ready(response) -> None:
    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "api_version": "v2",
        "checks": {"database": "error", "schema": "error"},
    }


def _write_revision(
    script_location: Path,
    *,
    revision: str,
    down_revision: str | None,
) -> None:
    versions = script_location / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / f"{revision}.py").write_text(
        "\n".join(
            (
                f"revision = {revision!r}",
                f"down_revision = {down_revision!r}",
                "branch_labels = None",
                "depends_on = None",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_health_is_database_independent() -> None:
    response = _client(
        _DatabaseMustNotBeUsed(),
        "track_anywhere_runtime",
    ).get("/api/v2/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v2"}


def test_missing_runtime_configuration_keeps_health_up_and_ready_closed() -> None:
    client = _client(None, None)

    health = client.get("/api/v2/health")
    ready = client.get("/api/v2/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "api_version": "v2"}
    _assert_redacted_not_ready(ready)


def test_ready_accepts_the_exact_migrated_pg17_runtime(
    pg_engine,
    migrated_postgres_database,
) -> None:
    response = _client(
        pg_engine,
        migrated_postgres_database.runtime_role,
    ).get("/api/v2/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api_version": "v2",
        "checks": {"database": "ok", "schema": "ok"},
    }


def test_ready_rejects_every_non_psycopg_driver() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    try:
        response = _client(engine, "track_anywhere_runtime").get("/api/v2/ready")
    finally:
        engine.dispose()

    _assert_redacted_not_ready(response)


def test_ready_requires_postgresql_17_exactly() -> None:
    response = _client(
        _VersionEngine(160_000),
        "track_anywhere_runtime",
    ).get("/api/v2/ready")

    _assert_redacted_not_ready(response)


def test_ready_requires_session_current_and_expected_runtime_to_match(
    pg_engine,
) -> None:
    response = _client(
        pg_engine,
        "not_the_connected_runtime",
    ).get("/api/v2/ready")

    _assert_redacted_not_ready(response)
    assert "not_the_connected_runtime" not in response.text


def test_ready_rejects_the_migrator_even_when_it_is_the_expected_login(
    migrated_postgres_database,
) -> None:
    engine = create_engine(migrated_postgres_database.migrator_url)
    try:
        response = _client(
            engine,
            migrated_postgres_database.migrator_role,
        ).get("/api/v2/ready")
    finally:
        engine.dispose()

    _assert_redacted_not_ready(response)


@pytest.mark.parametrize(
    "unsafe_flag",
    (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "rolinherit",
    ),
)
def test_runtime_role_validation_rejects_every_privileged_flag(
    unsafe_flag: str,
) -> None:
    identity = {
        "session_user": "runtime",
        "current_user": "runtime",
        "owner_role": "owner",
        "rolcanlogin": True,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
        "rolinherit": False,
        "direct_memberships": 0,
    }
    identity[unsafe_flag] = True

    with pytest.raises(system_module.ReadinessCheckError):
        system_module.validate_runtime_identity(identity, "runtime")


def test_ready_rejects_a_runtime_login_that_owns_the_database(
    migrated_postgres_database,
) -> None:
    database = migrated_postgres_database
    admin_engine = create_engine(database.admin_url)
    runtime_engine = create_engine(database.runtime_url)
    alter_to_runtime = text(
        f'alter database "{database.database_name}" owner to "{database.runtime_role}"'
    )
    restore_owner = text(
        f'alter database "{database.database_name}" owner to "{database.owner_role}"'
    )
    try:
        with admin_engine.begin() as connection:
            connection.execute(alter_to_runtime)
        response = _client(runtime_engine, database.runtime_role).get("/api/v2/ready")
    finally:
        runtime_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(restore_owner)
        admin_engine.dispose()

    _assert_redacted_not_ready(response)


def test_ready_rejects_a_database_without_the_v2_schema(
    empty_postgres_database,
) -> None:
    engine = create_engine(empty_postgres_database.runtime_url)
    try:
        response = _client(
            engine,
            empty_postgres_database.runtime_role,
        ).get("/api/v2/ready")
    finally:
        engine.dispose()

    _assert_redacted_not_ready(response)


def test_ready_requires_exactly_one_database_alembic_row_at_code_head(
    migrated_postgres_database,
) -> None:
    database = migrated_postgres_database
    admin_engine = create_engine(database.admin_url)
    runtime_engine = create_engine(database.runtime_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text("insert into alembic_version (version_num) values ('stale_head')")
            )
        response = _client(runtime_engine, database.runtime_role).get("/api/v2/ready")
    finally:
        runtime_engine.dispose()
        admin_engine.dispose()

    _assert_redacted_not_ready(response)


def test_ready_requires_the_exact_v2_generation_marker(
    migrated_postgres_database,
) -> None:
    database = migrated_postgres_database
    admin_engine = create_engine(database.admin_url)
    runtime_engine = create_engine(database.runtime_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text("delete from v2_schema_metadata"))
        response = _client(runtime_engine, database.runtime_role).get("/api/v2/ready")
    finally:
        runtime_engine.dispose()
        admin_engine.dispose()

    _assert_redacted_not_ready(response)


def test_code_head_is_discovered_dynamically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_location = tmp_path / "alembic"
    _write_revision(
        script_location,
        revision="dynamic_head",
        down_revision=None,
    )
    monkeypatch.setattr(
        system_module,
        "_ALEMBIC_SCRIPT_LOCATION",
        script_location,
    )

    assert system_module.current_alembic_head() == "dynamic_head"


def test_code_head_fails_closed_when_migrations_have_multiple_heads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script_location = tmp_path / "alembic"
    _write_revision(script_location, revision="head_a", down_revision=None)
    _write_revision(script_location, revision="head_b", down_revision=None)
    monkeypatch.setattr(
        system_module,
        "_ALEMBIC_SCRIPT_LOCATION",
        script_location,
    )

    with pytest.raises(system_module.ReadinessCheckError):
        system_module.current_alembic_head()
