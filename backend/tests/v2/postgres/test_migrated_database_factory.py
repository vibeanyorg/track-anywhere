from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from backend.tests.v2.postgres.test_database_factory import (
    FACTORY_SCRIPT,
    _database_exists,
    _run_factory_cli,
)
from backend.tests.v2.postgres_factory import current_v2_head


def _factory_database_names(factory) -> set[str]:
    prefix = f"ta_v2_{factory.worker_id[:10]}_{factory.test_uuid}_"
    engine = create_engine(factory.config.admin_url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "select datname from pg_database "
                        "where left(datname, length(:prefix)) = :prefix"
                    ),
                    {"prefix": prefix},
                ).scalars()
            )
    finally:
        engine.dispose()


def _database_names_with_token(factory, token: str) -> set[str]:
    engine = create_engine(factory.config.admin_url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "select datname from pg_database "
                        "where datname like 'ta_v2_%' and strpos(datname, :token) > 0"
                    ),
                    {"token": token},
                ).scalars()
            )
    finally:
        engine.dispose()


def test_factory_creates_migrated_v2_database_for_runtime(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="migrated", schema="v2")

    with create_engine(database.runtime_url).connect() as connection:
        assert (
            connection.execute(text("select session_user")).scalar_one()
            == database.runtime_role
        )
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == current_v2_head()
        )
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )
        with pytest.raises(ProgrammingError, match="permission denied"):
            connection.execute(text("delete from v2_schema_metadata"))


def test_worker_v2_template_is_frozen(
    migrated_postgres_template,
    postgres_cluster_config,
) -> None:
    engine = create_engine(postgres_cluster_config.admin_url)
    try:
        with engine.connect() as connection:
            template = (
                connection.execute(
                    text(
                        "select datallowconn, pg_catalog.pg_get_userbyid(datdba) "
                        "as owner_role "
                        "from pg_catalog.pg_database where datname = :name"
                    ),
                    {"name": migrated_postgres_template.database_name},
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert template == {
        "datallowconn": False,
        "owner_role": postgres_cluster_config.owner_role,
    }


def test_factory_clones_worker_template_without_rerunning_alembic(
    postgres_database_factory,
    migrated_postgres_template,
    monkeypatch,
) -> None:
    def fail_migration(_database) -> None:
        raise AssertionError("clone unexpectedly reran Alembic")

    monkeypatch.setattr(postgres_database_factory, "_upgrade_to_v2", fail_migration)
    database = postgres_database_factory.clone_v2(
        purpose="clone", template=migrated_postgres_template
    )

    with create_engine(database.runtime_url).connect() as connection:
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == current_v2_head()
        )
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )


def test_factory_rejects_an_unfrozen_v2_template(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="unfrozen", schema="v2")

    with pytest.raises(RuntimeError, match="must reject connections"):
        postgres_database_factory.clone_v2(
            purpose="invalid_clone",
            template=database,
        )


def test_migrated_pg_engine_uses_runtime_role(
    pg_engine, postgres_cluster_config
) -> None:
    with pg_engine.connect() as connection:
        assert connection.execute(text("select session_user")).scalar_one() == (
            postgres_cluster_config.runtime_role
        )
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )


def test_migrated_source_and_target_are_independent(
    migrated_postgres_source_target,
) -> None:
    source, target = migrated_postgres_source_target

    assert source.database_name != target.database_name
    with create_engine(source.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{source.owner_role}"'))
        connection.execute(text("create table source_marker(id integer)"))
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )
    with create_engine(target.runtime_url).connect() as connection:
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text("select to_regclass('public.source_marker')")
            ).scalar_one()
            is None
        )


def test_migrated_databases_can_be_cleaned_up_independently(
    postgres_database_factory,
) -> None:
    source = postgres_database_factory.create(purpose="source", schema="v2")
    target = postgres_database_factory.create(purpose="target", schema="v2")

    postgres_database_factory.drop(source)

    assert not _database_exists(
        postgres_database_factory.config.admin_url, source.database_name
    )
    assert _database_exists(
        postgres_database_factory.config.admin_url, target.database_name
    )
    with create_engine(target.runtime_url).connect() as connection:
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )


def test_factory_drops_database_if_migration_fails(
    postgres_database_factory, monkeypatch
) -> None:
    sibling = postgres_database_factory.create(purpose="sibling", schema="v2")
    databases_before = _factory_database_names(postgres_database_factory)

    def fail_migration(_database) -> None:
        raise RuntimeError("intentional migration failure")

    monkeypatch.setattr(postgres_database_factory, "_upgrade_to_v2", fail_migration)
    with pytest.raises(RuntimeError, match="intentional migration failure"):
        postgres_database_factory.create(purpose="failed_migration", schema="v2")

    assert _factory_database_names(postgres_database_factory) == databases_before
    with create_engine(sibling.runtime_url).connect() as connection:
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )


def test_factory_drops_clone_if_validation_fails(
    postgres_database_factory,
    migrated_postgres_template,
    monkeypatch,
) -> None:
    databases_before = _factory_database_names(postgres_database_factory)

    def fail_validation(_database) -> None:
        raise RuntimeError("intentional clone validation failure")

    monkeypatch.setattr(postgres_database_factory, "_validate_v2", fail_validation)
    with pytest.raises(RuntimeError, match="intentional clone validation failure"):
        postgres_database_factory.clone_v2(
            purpose="failed_clone", template=migrated_postgres_template
        )

    assert _factory_database_names(postgres_database_factory) == databases_before


def test_v2_create_cli_emits_only_requested_runtime_dsn() -> None:
    created = _run_factory_cli(
        "create",
        "--purpose",
        "migrated_cli",
        "--schema",
        "v2",
        "--emit-role",
        "runtime",
    )

    assert created.returncode == 0, created.stderr
    assert created.stderr == ""
    created_url = created.stdout.strip()
    runtime_username = make_url(created_url).username
    assert runtime_username is not None
    try:
        with create_engine(created_url).connect() as connection:
            assert (
                connection.execute(text("select session_user")).scalar_one()
                == runtime_username
            )
            assert (
                connection.execute(
                    text("select schema_generation from v2_schema_metadata")
                ).scalar_one()
                == 2
            )
    finally:
        dropped = _run_factory_cli("drop", "--url", created_url)

    assert dropped.returncode == 0, dropped.stderr


def test_create_cli_drops_database_when_stdout_delivery_breaks(
    postgres_database_factory,
) -> None:
    purpose = f"bp_{uuid.uuid4().hex[:12]}"
    assert not _database_names_with_token(postgres_database_factory, purpose)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    process = subprocess.Popen(
        [
            sys.executable,
            str(FACTORY_SCRIPT),
            "create",
            "--purpose",
            purpose,
            "--schema",
            "v2",
            "--emit-role",
            "runtime",
        ],
        stdout=write_fd,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    os.close(write_fd)
    _, stderr = process.communicate(timeout=30)
    orphaned = _database_names_with_token(postgres_database_factory, purpose)
    try:
        assert process.returncode != 0
        assert orphaned == set()
        assert "Traceback" not in stderr
        assert "track_anywhere_runtime_test" not in stderr
    finally:
        for database_name in orphaned:
            postgres_database_factory.drop(database_name)
