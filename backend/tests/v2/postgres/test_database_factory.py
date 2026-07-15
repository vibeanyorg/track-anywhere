from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextlib import suppress
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError

from backend.tests.v2.postgres_factory import (
    ADMIN_URL_ENV,
    EXTERNAL_DATABASE_GATE_ENV,
    MIGRATOR_URL_ENV,
    RUNTIME_URL_ENV,
    ClusterConfig,
    PostgresDatabaseFactory,
    render_libpq_url,
    render_read_only_url,
)


FACTORY_SCRIPT = Path(__file__).resolve().parents[1] / "postgres_factory.py"


def _database_exists(admin_url: str, database_name: str) -> bool:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "select exists(select 1 from pg_database where datname = :name)"
                    ),
                    {"name": database_name},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _run_factory_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FACTORY_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _render_url(url) -> str:
    return url.render_as_string(hide_password=False)


@contextmanager
def _temporary_role_config(
    postgres_cluster_config,
) -> Iterator[tuple[ClusterConfig, dict[str, str]]]:
    suffix = uuid.uuid4().hex[:10]
    roles = {
        "owner": f"ta_bad_owner_{suffix}",
        "migrator": f"ta_bad_migrator_{suffix}",
        "runtime": f"ta_bad_runtime_{suffix}",
        "extra": f"ta_bad_extra_{suffix}",
    }
    password = f"temporary_{suffix}"
    admin_engine = create_engine(
        postgres_cluster_config.admin_url, isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'CREATE ROLE "{roles["owner"]}" NOLOGIN '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
            )
            connection.exec_driver_sql(
                f"CREATE ROLE \"{roles['migrator']}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
            )
            connection.exec_driver_sql(
                f"CREATE ROLE \"{roles['runtime']}\" LOGIN PASSWORD '{password}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
            )
            connection.exec_driver_sql(
                f'CREATE ROLE "{roles["extra"]}" NOLOGIN '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
            )
            connection.exec_driver_sql(
                f'GRANT "{roles["owner"]}" TO "{roles["migrator"]}" '
                "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
            )

        environment = {
            EXTERNAL_DATABASE_GATE_ENV: "1",
            ADMIN_URL_ENV: postgres_cluster_config.admin_url,
            MIGRATOR_URL_ENV: _render_url(
                postgres_cluster_config.migrator_base_url.set(
                    username=roles["migrator"],
                    password=password,
                )
            ),
            RUNTIME_URL_ENV: _render_url(
                postgres_cluster_config.runtime_base_url.set(
                    username=roles["runtime"],
                    password=password,
                )
            ),
            "TRACK_ANYWHERE_OWNER_ROLE": roles["owner"],
            "TRACK_ANYWHERE_MIGRATOR_ROLE": roles["migrator"],
            "TRACK_ANYWHERE_RUNTIME_ROLE": roles["runtime"],
        }
        yield ClusterConfig.from_env(environment), roles
    finally:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'ALTER ROLE "{roles["runtime"]}" NOBYPASSRLS NOREPLICATION'
            )
            connection.exec_driver_sql(
                f'ALTER ROLE "{roles["migrator"]}" NOBYPASSRLS NOREPLICATION'
            )
            connection.exec_driver_sql(f'ALTER ROLE "{roles["extra"]}" NOCREATEDB')
            connection.exec_driver_sql(
                f'REVOKE "{roles["extra"]}" FROM "{roles["owner"]}"'
            )
            connection.exec_driver_sql(
                f'REVOKE "{roles["extra"]}" FROM "{roles["runtime"]}"'
            )
            connection.exec_driver_sql(
                f'REVOKE "{roles["extra"]}" FROM "{roles["migrator"]}"'
            )
            connection.exec_driver_sql(
                f'REVOKE "{roles["owner"]}" FROM "{roles["migrator"]}"'
            )
            connection.exec_driver_sql(f'DROP ROLE "{roles["runtime"]}"')
            connection.exec_driver_sql(f'DROP ROLE "{roles["migrator"]}"')
            connection.exec_driver_sql(f'DROP ROLE "{roles["owner"]}"')
            connection.exec_driver_sql(f'DROP ROLE "{roles["extra"]}"')
        admin_engine.dispose()


def test_cluster_config_requires_all_three_role_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL",
        "TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL",
        "TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="missing required PostgreSQL 17 test URL"):
        ClusterConfig.from_env()


def test_cluster_config_rejects_mismatched_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL",
        "postgresql+psycopg://track_anywhere_runtime:runtime@localhost:15543/postgres",
    )

    with pytest.raises(ValueError, match="same loopback host and port"):
        ClusterConfig.from_env()


def test_cluster_config_rejects_reused_login_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_url = os.environ["TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL"]
    monkeypatch.setenv("TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL", admin_url)

    with pytest.raises(ValueError, match="three distinct login identities"):
        ClusterConfig.from_env()


@pytest.mark.parametrize(
    "query",
    (
        "host=203.0.113.9",
        "dbname=postgres",
        "user=track_anywhere",
    ),
)
def test_cluster_config_rejects_connection_query_overrides(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    unsafe = os.environ[RUNTIME_URL_ENV] + f"?{query}"
    monkeypatch.setenv(RUNTIME_URL_ENV, unsafe)

    with pytest.raises(ValueError, match="query parameters"):
        ClusterConfig.from_env()


def test_cluster_config_requires_exact_psycopg_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        RUNTIME_URL_ENV,
        "postgresql://track_anywhere_runtime:runtime@127.0.0.1:15543/postgres",
    )

    with pytest.raises(ValueError, match=r"postgresql\+psycopg"):
        ClusterConfig.from_env()


def test_libpq_url_uses_sqlalchemy_rendering_and_preserves_encoded_values() -> None:
    source = (
        "postgresql+psycopg://ledger:p%40ss%2Fword@127.0.0.1:15543/"
        "db%2Fencoded?application_name=worker%2Fv2&options=-c%20lock_timeout%3D5s"
    )

    rendered = render_libpq_url(source, host="postgres", port=5432)
    parsed = make_url(rendered)

    assert parsed.drivername == "postgresql"
    assert (parsed.host, parsed.port) == ("postgres", 5432)
    assert (parsed.username, parsed.password, parsed.database) == (
        "ledger",
        "p@ss/word",
        "db%2Fencoded",
    )
    assert dict(parsed.query) == {
        "application_name": "worker/v2",
        "options": "-c lock_timeout=5s",
    }
    assert "p%40ss%2Fword" in rendered
    assert "db%2Fencoded" in rendered
    assert "worker%2Fv2" in rendered


@pytest.mark.parametrize(
    ("source", "host", "port", "message"),
    (
        (
            "postgresql+psycopg://u:p@203.0.113.9/db",
            "postgres",
            5432,
            "loopback",
        ),
        (
            "postgresql+psycopg://u:p@127.0.0.1/db",
            "not-postgres",
            5432,
            "fixed postgres:5432",
        ),
        (
            "postgresql+psycopg://u:p@127.0.0.1/db",
            "postgres",
            15432,
            "fixed postgres:5432",
        ),
        (
            "postgresql+psycopg://u:p@127.0.0.1/db?host=203.0.113.9",
            "postgres",
            5432,
            "must not override connection identity",
        ),
    ),
)
def test_libpq_url_rejects_untrusted_source_or_target(
    source: str, host: str, port: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        render_libpq_url(source, host=host, port=port)


def test_read_only_url_sets_server_enforced_transaction_default() -> None:
    rendered = render_read_only_url(
        "postgresql+psycopg://ledger:p%40ss@localhost:15543/source"
    )
    parsed = make_url(rendered)

    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.password == "p@ss"
    assert parsed.query["options"] == "-c default_transaction_read_only=on"


def test_malformed_cluster_dsn_is_redacted_and_has_no_cli_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "malformed-password-sentinel"
    malformed = f"not a valid DSN containing {secret}"
    monkeypatch.setenv(RUNTIME_URL_ENV, malformed)

    with pytest.raises(ValueError) as error:
        ClusterConfig.from_env()
    assert secret not in str(error.value)
    assert "\n" not in str(error.value)

    result = _run_factory_cli("role-name", "--kind", "runtime")
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert secret not in result.stderr
    assert len(result.stderr.splitlines()) == 1


@pytest.mark.parametrize("unsafe_kind", ("query", "other_port", "malformed"))
def test_drop_cli_rejects_untrusted_urls_without_traceback(unsafe_kind: str) -> None:
    runtime_base = make_url(os.environ[RUNTIME_URL_ENV])
    safe_child = runtime_base.set(database="ta_v2_drop_probe")
    secret = runtime_base.password or "track_anywhere_runtime_test"
    if unsafe_kind == "query":
        unsafe_url = _render_url(safe_child) + "?host=203.0.113.9"
    elif unsafe_kind == "other_port":
        unsafe_url = _render_url(safe_child.set(port=(safe_child.port or 5432) + 1))
    else:
        unsafe_url = f"not a valid drop DSN containing {secret}"

    result = _run_factory_cli("drop", "--url", unsafe_url)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert secret not in result.stderr
    assert len(result.stderr.splitlines()) == 1


def test_drop_cli_rejects_64_byte_name_without_truncating_a_63_byte_database(
    postgres_cluster_config,
) -> None:
    prefix = f"ta_v2_{uuid.uuid4().hex}_"
    database_name = prefix + ("a" * (63 - len(prefix)))
    overlong_name = database_name + "b"
    admin_engine = create_engine(
        postgres_cluster_config.admin_url,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'CREATE DATABASE "{database_name}" '
                f'OWNER "{postgres_cluster_config.owner_role}"'
            )

        drop_url = _render_url(
            postgres_cluster_config.runtime_base_url.set(database=overlong_name)
        )
        result = _run_factory_cli("drop", "--url", drop_url)
        still_exists = _database_exists(
            postgres_cluster_config.admin_url, database_name
        )

        assert (result.returncode, still_exists) == (2, True)
        assert "Traceback" not in result.stderr
        assert overlong_name not in result.stderr
        assert len(result.stderr.splitlines()) == 1
    finally:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
            )
        admin_engine.dispose()


def test_assert_absent_cli_fails_closed_until_factory_database_is_dropped(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="absence")

    present = _run_factory_cli("assert-absent", "--url", database.runtime_url)
    assert present.returncode == 2
    assert "still exists" in present.stderr
    assert "Traceback" not in present.stderr
    assert database.runtime_url not in present.stderr

    postgres_database_factory.drop(database)
    absent = _run_factory_cli("assert-absent", "--url", database.runtime_url)
    assert absent.returncode == 0
    assert absent.stdout == ""
    assert absent.stderr == ""


def test_factory_creates_empty_database_with_isolated_name(
    postgres_database_factory,
) -> None:
    first = postgres_database_factory.create(purpose="lifecycle")
    second = postgres_database_factory.create(purpose="lifecycle")

    assert first.database_name != second.database_name
    assert postgres_database_factory.worker_id in first.database_name
    assert postgres_database_factory.test_uuid in first.database_name
    assert len(first.database_name.encode("ascii")) <= 63
    assert _database_exists(first.admin_url, first.database_name)
    assert make_url(first.admin_url).set(
        database=postgres_database_factory.config.admin_base_url.database
    ) == (postgres_database_factory.config.admin_base_url)
    assert (
        make_url(first.migrator_url).set(
            database=postgres_database_factory.config.migrator_base_url.database
        )
        == postgres_database_factory.config.migrator_base_url
    )
    assert (
        make_url(first.runtime_url).set(
            database=postgres_database_factory.config.runtime_base_url.database
        )
        == postgres_database_factory.config.runtime_base_url
    )
    with create_engine(first.runtime_url).connect() as connection:
        assert (
            connection.execute(text("select current_database()")).scalar_one()
            == first.database_name
        )
        assert (
            connection.execute(
                text(
                    "select count(*) from pg_catalog.pg_tables "
                    "where schemaname not in ('pg_catalog', 'information_schema')"
                )
            ).scalar_one()
            == 0
        )


def test_factory_names_are_isolated_by_worker_and_test_uuid(
    postgres_cluster_config,
) -> None:
    factories = (
        PostgresDatabaseFactory(
            postgres_cluster_config, worker_id="gw0", test_uuid="a" * 16
        ),
        PostgresDatabaseFactory(
            postgres_cluster_config, worker_id="gw1", test_uuid="a" * 16
        ),
        PostgresDatabaseFactory(
            postgres_cluster_config, worker_id="gw0", test_uuid="b" * 16
        ),
    )
    try:
        databases = tuple(factory.create(purpose="isolation") for factory in factories)
        assert len({database.database_name for database in databases}) == 3
        assert "gw0" in databases[0].database_name
        assert "gw1" in databases[1].database_name
        assert "a" * 16 in databases[0].database_name
        assert "b" * 16 in databases[2].database_name
    finally:
        for factory in factories:
            factory.close()


def test_create_collision_never_drops_the_existing_database(
    postgres_cluster_config,
) -> None:
    first_factory = PostgresDatabaseFactory(
        postgres_cluster_config,
        worker_id="collision",
        test_uuid="c" * 16,
    )
    second_factory = PostgresDatabaseFactory(
        postgres_cluster_config,
        worker_id="collision",
        test_uuid="c" * 16,
    )
    first = first_factory.create(purpose="same")
    try:
        with pytest.raises(ProgrammingError, match="already exists"):
            second_factory.create(purpose="same")

        assert _database_exists(postgres_cluster_config.admin_url, first.database_name)
        with create_engine(first.runtime_url).connect() as connection:
            assert (
                connection.execute(text("select current_database()")).scalar_one()
                == first.database_name
            )
    finally:
        second_factory.close()
        first_factory.close()


def test_factory_uses_separate_owner_migrator_and_runtime_roles(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="roles")

    with create_engine(database.admin_url).connect() as connection:
        row = connection.execute(
            text(
                """
                select owner.rolname,
                       owner.rolcanlogin, owner.rolsuper, owner.rolcreatedb, owner.rolcreaterole,
                       owner.rolreplication, owner.rolbypassrls,
                       migrator.rolcanlogin, migrator.rolsuper, migrator.rolcreatedb, migrator.rolcreaterole,
                       migrator.rolreplication, migrator.rolbypassrls,
                       runtime.rolcanlogin, runtime.rolsuper, runtime.rolcreatedb, runtime.rolcreaterole,
                       runtime.rolreplication, runtime.rolbypassrls,
                       not exists (
                           select 1 from aclexplode(database.datacl) acl where acl.grantee = 0
                       ) as public_has_no_database_privileges
                  from pg_database database
                  join pg_roles owner on owner.oid = database.datdba
                  join pg_roles migrator on migrator.rolname = :migrator
                  join pg_roles runtime on runtime.rolname = :runtime
                 where database.datname = :database
                """
            ),
            {
                "database": database.database_name,
                "migrator": database.migrator_role,
                "runtime": database.runtime_role,
            },
        ).one()

    assert tuple(row) == (
        database.owner_role,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        True,
    )

    with create_engine(database.admin_url).connect() as connection:
        memberships = connection.execute(
            text(
                """
                select member.rolname, granted.rolname,
                       membership.admin_option,
                       membership.inherit_option,
                       membership.set_option
                  from pg_auth_members membership
                  join pg_roles member on member.oid = membership.member
                  join pg_roles granted on granted.oid = membership.roleid
                 where member.rolname in (:owner, :migrator, :runtime)
                 order by member.rolname, granted.rolname
                """
            ),
            {
                "owner": database.owner_role,
                "migrator": database.migrator_role,
                "runtime": database.runtime_role,
            },
        ).all()

    assert [tuple(row) for row in memberships] == [
        (database.migrator_role, database.owner_role, False, False, True)
    ]

    with create_engine(database.migrator_url).connect() as connection:
        assert (
            connection.execute(text("select session_user")).scalar_one()
            == database.migrator_role
        )
        connection.execute(text(f'SET ROLE "{database.owner_role}"'))
        assert (
            connection.execute(text("select current_user")).scalar_one()
            == database.owner_role
        )

    with create_engine(database.runtime_url).connect() as connection:
        assert (
            connection.execute(text("select session_user")).scalar_one()
            == database.runtime_role
        )
        assert (
            connection.execute(text("select current_user")).scalar_one()
            == database.runtime_role
        )


@pytest.mark.parametrize(
    "corruption",
    (
        "runtime_bypassrls",
        "runtime_extra_membership",
        "migrator_extra_membership",
        "owner_upstream_membership",
    ),
)
def test_factory_rejects_unsafe_independent_roles_before_database_creation(
    postgres_cluster_config,
    corruption: str,
) -> None:
    with _temporary_role_config(postgres_cluster_config) as (config, roles):
        admin_engine = create_engine(config.admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                if corruption == "runtime_bypassrls":
                    connection.exec_driver_sql(
                        f'ALTER ROLE "{roles["runtime"]}" BYPASSRLS'
                    )
                elif corruption == "runtime_extra_membership":
                    connection.exec_driver_sql(
                        f'GRANT "{roles["extra"]}" TO "{roles["runtime"]}"'
                    )
                elif corruption == "migrator_extra_membership":
                    connection.exec_driver_sql(
                        f'GRANT "{roles["extra"]}" TO "{roles["migrator"]}"'
                    )
                else:
                    connection.exec_driver_sql(
                        f'ALTER ROLE "{roles["extra"]}" CREATEDB'
                    )
                    connection.exec_driver_sql(
                        f'GRANT "{roles["extra"]}" TO "{roles["owner"]}"'
                    )

            factory = PostgresDatabaseFactory(
                config, worker_id="bad", test_uuid=uuid.uuid4().hex
            )
            expected_database = (
                f"ta_v2_{factory.worker_id}_{factory.test_uuid}_unsafe_role_1"
            )
            try:
                with pytest.raises(RuntimeError, match="role|membership|BYPASSRLS"):
                    factory.create(purpose="unsafe_role")
                assert not _database_exists(config.admin_url, expected_database)
            finally:
                factory.close()
        finally:
            admin_engine.dispose()


@pytest.mark.parametrize(
    "statement",
    (
        "create schema forbidden_schema",
        "create table public.forbidden_table(id integer)",
    ),
)
def test_runtime_cannot_create_schema_objects(
    postgres_database_factory, statement: str
) -> None:
    database = postgres_database_factory.create(purpose="runtime_permissions")

    with create_engine(database.runtime_url).connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            connection.execute(text(statement))


def test_runtime_cannot_set_role_to_owner(postgres_database_factory) -> None:
    database = postgres_database_factory.create(purpose="runtime_role")

    with create_engine(database.runtime_url).connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied to set role"):
            connection.execute(text(f'SET ROLE "{database.owner_role}"'))


def test_source_and_target_databases_are_independent(
    empty_postgres_source_target,
) -> None:
    source, target = empty_postgres_source_target

    assert source.database_name != target.database_name
    with create_engine(source.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{source.owner_role}"'))
        connection.execute(text("create table source_marker(id integer)"))

    with create_engine(target.runtime_url).connect() as connection:
        assert (
            connection.execute(
                text("select to_regclass('public.source_marker')")
            ).scalar_one()
            is None
        )


def test_created_database_is_visible_to_a_child_process(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="child_process")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from sqlalchemy import create_engine, text; "
                "engine = create_engine(sys.argv[1]); "
                "connection = engine.connect(); "
                "print(connection.execute(text('select current_database()')).scalar_one()); "
                "connection.close(); engine.dispose()"
            ),
            database.runtime_url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{database.database_name}\n"


def test_drop_terminates_connections_and_removes_database(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="cleanup")
    engine = create_engine(database.runtime_url)
    connection = engine.connect()

    postgres_database_factory.drop(database)

    assert not _database_exists(
        postgres_database_factory.config.admin_url, database.database_name
    )
    with pytest.raises(DBAPIError):
        connection.execute(text("select 1"))
    with suppress(DBAPIError):
        connection.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("kind", "expected_environment_name"),
    (
        ("owner", "TRACK_ANYWHERE_OWNER_ROLE"),
        ("migrator", "TRACK_ANYWHERE_MIGRATOR_ROLE"),
        ("runtime", "TRACK_ANYWHERE_RUNTIME_ROLE"),
    ),
)
def test_role_name_cli_prints_only_requested_role(
    kind: str, expected_environment_name: str
) -> None:
    result = _run_factory_cli("role-name", "--kind", kind)

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout
        == f"{os.environ.get(expected_environment_name, PostgresDatabaseFactory.default_role_name(kind))}\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize("emit_role", ("migrator", "runtime"))
def test_create_and_drop_cli_emit_only_the_requested_dsn(emit_role: str) -> None:
    created = _run_factory_cli(
        "create",
        "--purpose",
        "cli",
        "--schema",
        "empty",
        "--emit-role",
        emit_role,
    )

    assert created.returncode == 0, created.stderr
    assert created.stderr == ""
    created_url = created.stdout.strip()
    try:
        assert created_url.startswith("postgresql+psycopg://")
        base_url = os.environ[
            f"TRACK_ANYWHERE_TEST_POSTGRES_{emit_role.upper()}_BASE_URL"
        ]
        assert make_url(created_url).username == make_url(base_url).username
    finally:
        dropped = _run_factory_cli("drop", "--url", created_url)

    assert dropped.returncode == 0, dropped.stderr
    assert dropped.stdout == ""
    assert dropped.stderr == ""


def test_create_cli_accepts_the_v2_schema_boundary() -> None:
    created = _run_factory_cli(
        "create",
        "--purpose",
        "v2_boundary",
        "--schema",
        "v2",
        "--emit-role",
        "runtime",
    )

    assert created.returncode == 0, created.stderr
    created_url = created.stdout.strip()
    try:
        with create_engine(created_url).connect() as connection:
            assert (
                connection.execute(
                    text("select schema_generation from v2_schema_metadata")
                ).scalar_one()
                == 2
            )
    finally:
        dropped = _run_factory_cli("drop", "--url", created_url)
    assert dropped.returncode == 0, dropped.stderr
