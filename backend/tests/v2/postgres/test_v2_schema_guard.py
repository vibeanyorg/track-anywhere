from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from backend.tests.v2.postgres.test_database_factory import _temporary_role_config
from backend.tests.v2.postgres_factory import (
    PostgresDatabaseFactory,
    current_v2_head,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPOSITORY_ROOT / "alembic.ini"
DATABASE_URL_ENV = "TRACK_ANYWHERE_DATABASE_URL"
RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
V2_MODEL_TABLES = {
    "account_balances",
    "accounts",
    "assets",
    "auth_identities",
    "backfill_checkpoints",
    "backfill_quarantine",
    "backfill_review_contracts",
    "backfill_seals",
    "backfill_source_receipts",
    "book_members",
    "book_event_heads",
    "books",
    "browser_sessions",
    "categories",
    "category_versions",
    "command_receipts",
    "credentials",
    "credit_card_transactions",
    "event_stream_heads",
    "journal_postings",
    "journal_transactions",
    "investment_lot_allocations",
    "investment_lots",
    "ledger_events",
    "monthly_category_summaries",
    "oauth_authorization_grants",
    "oauth_client_redirect_uris",
    "oauth_clients",
    "oauth_device_grants",
    "outbox_messages",
    "projection_checkpoints",
    "projection_dirty_periods",
    "projection_failures",
    "projection_generations",
    "password_accounts",
    "protected_description_sidecars",
    "reporting_lines",
    "synchronous_projection_applied_events",
    "synchronous_projection_event_types",
    "transaction_external_references",
    "transaction_reversals",
    "users",
    "v2_schema_metadata",
}
V2_RELATION_NAMES = sorted(
    {"alembic_version", "ledger_global_sequence", *V2_MODEL_TABLES}
)


def _run_alembic(
    *arguments: str,
    database_url: str | None = None,
    runtime_role: str | None = None,
    config_path: Path = ALEMBIC_INI,
    database_url_present: bool = True,
    runtime_role_present: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop(DATABASE_URL_ENV, None)
    environment.pop(RUNTIME_ROLE_ENV, None)
    if database_url_present:
        environment[DATABASE_URL_ENV] = "" if database_url is None else database_url
    if runtime_role_present:
        environment[RUNTIME_ROLE_ENV] = "" if runtime_role is None else runtime_role
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(config_path), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_programmatic_alembic(
    operation: str,
    *,
    database_url: str,
    runtime_role: str,
) -> subprocess.CompletedProcess[str]:
    script = """
import sys
from alembic import command
from alembic.config import Config

config = Config(sys.argv[1])
if sys.argv[2] == "stamp":
    command.stamp(config, "head")
elif sys.argv[2] == "current":
    command.current(config)
else:
    raise AssertionError("unsupported operation")
"""
    environment = os.environ.copy()
    environment[DATABASE_URL_ENV] = database_url
    environment[RUNTIME_ROLE_ENV] = runtime_role
    return subprocess.run(
        [sys.executable, "-c", script, str(ALEMBIC_INI), operation],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _create_standard_version_table(database) -> None:
    with create_engine(database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{database.owner_role}"'))
        connection.execute(
            text(
                "create table public.alembic_version "
                "(version_num varchar(32) not null, "
                "constraint alembic_version_pkc primary key (version_num))"
            )
        )


def _write_alembic_config(tmp_path: Path, database_url: str) -> Path:
    config = ALEMBIC_INI.read_text(encoding="utf-8")
    lines = []
    replaced = False
    for line in config.splitlines():
        if line.startswith("script_location ="):
            line = f"script_location = {REPOSITORY_ROOT / 'alembic'}"
        if line.startswith("sqlalchemy.url ="):
            line = f"sqlalchemy.url = {database_url}"
            replaced = True
        lines.append(line)
    if not replaced:
        lines.insert(
            lines.index("[post_write_hooks]"), f"sqlalchemy.url = {database_url}\n"
        )
    path = tmp_path / "alembic.ini"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _relation_names(database_url: str) -> list[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        """
                        select relation.relname
                          from pg_catalog.pg_class relation
                          join pg_catalog.pg_namespace namespace
                            on namespace.oid = relation.relnamespace
                         where namespace.nspname = 'public'
                           and relation.relkind in ('r', 'p', 'f', 'S', 'v', 'm')
                         order by relation.relname
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def _public_acl_state(database_url: str) -> tuple[str | None, int]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    """
                    select namespace.nspacl::text,
                           (select count(*) from pg_catalog.pg_default_acl)
                      from pg_catalog.pg_namespace namespace
                     where namespace.nspname = 'public'
                    """
                )
            ).one()
    finally:
        engine.dispose()


def _runtime_ddl_state(database, runtime_role: str) -> tuple[object, ...]:
    engine = create_engine(database.admin_url)
    try:
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        """
                        select has_database_privilege(:runtime, current_database(), 'CONNECT'),
                               has_database_privilege(:runtime, current_database(), 'CREATE'),
                               has_database_privilege(:runtime, current_database(), 'TEMPORARY'),
                               has_schema_privilege(:runtime, 'public', 'USAGE'),
                               has_schema_privilege(:runtime, 'public', 'CREATE'),
                               database.datacl::text,
                               namespace.nspacl::text,
                               (select count(*) from pg_catalog.pg_default_acl)
                          from pg_catalog.pg_database database
                          join pg_catalog.pg_namespace namespace
                            on namespace.nspname = 'public'
                         where database.datname = current_database()
                        """
                    ),
                    {"runtime": runtime_role},
                ).one()
            )
    finally:
        engine.dispose()


def _schema_object_exists(
    database_url: str, object_kind: str, object_name: str
) -> bool:
    statements = {
        "type": """
            select exists(
                select 1 from pg_catalog.pg_type object
                join pg_catalog.pg_namespace namespace on namespace.oid = object.typnamespace
                where namespace.nspname = 'public' and object.typname = :name
                  and object.typrelid = 0
            )
        """,
        "function": """
            select exists(
                select 1 from pg_catalog.pg_proc object
                join pg_catalog.pg_namespace namespace on namespace.oid = object.pronamespace
                where namespace.nspname = 'public' and object.proname = :name
            )
        """,
    }
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(statements[object_kind]),
                    {"name": object_name},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_v2_metadata_uses_naming_convention_and_contains_generation_marker() -> None:
    base = importlib.import_module("track_anywhere.infrastructure.db.base")
    base.load_v2_models()

    assert base.V2Base.metadata.naming_convention == {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
    assert set(base.V2Base.metadata.tables) == V2_MODEL_TABLES


def test_model_loader_ignores_only_the_absent_models_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = importlib.import_module("track_anywhere.infrastructure.db.base")

    def missing_models(name: str):
        raise ModuleNotFoundError("models package is not present yet", name=name)

    monkeypatch.setattr(base.importlib, "import_module", missing_models)
    base.load_v2_models()

    def missing_inner_dependency(name: str):
        raise ModuleNotFoundError(
            "inner dependency failed", name="secret_inner_dependency"
        )

    monkeypatch.setattr(base.importlib, "import_module", missing_inner_dependency)
    with pytest.raises(ModuleNotFoundError, match="inner dependency failed"):
        base.load_v2_models()


def test_engine_requires_exact_postgresql_psycopg_and_redacts_bad_urls() -> None:
    engine_module = importlib.import_module("track_anywhere.infrastructure.db.engine")
    secret = "engine-secret-sentinel"

    for unsafe in (
        "sqlite:///:memory:",
        "postgresql://user:password@127.0.0.1/database",
        "postgresql+psycopg://user:password@127.0.0.1/database?host=203.0.113.9",
        f"not a database URL containing {secret}",
    ):
        with pytest.raises(ValueError) as error:
            engine_module.create_v2_engine(unsafe)
        assert secret not in str(error.value)
        assert unsafe not in str(error.value)


@pytest.mark.parametrize(
    "query",
    (
        "sslmode=require",
        "sslmode=verify-ca&channel_binding=prefer&connect_timeout=1",
        "sslmode=verify-full&channel_binding=require&connect_timeout=60",
    ),
)
def test_engine_allows_only_safe_connection_query_parameters(query: str) -> None:
    engine_module = importlib.import_module("track_anywhere.infrastructure.db.engine")
    engine = engine_module.create_v2_engine(
        f"postgresql+psycopg://user:password@database.example/ledger?{query}"
    )
    engine.dispose()


@pytest.mark.parametrize(
    "query",
    (
        "sslmode=disable",
        "sslmode=prefer",
        "channel_binding=disable",
        "channel_binding=unexpected",
        "connect_timeout=0",
        "connect_timeout=61",
        "connect_timeout=not-a-number",
        "host=203.0.113.9",
        "user=admin",
        "dbname=other",
        "options=-csearch_path%3Devil",
        "sslmode=require&sslmode=verify-full",
    ),
)
def test_engine_rejects_unsafe_duplicate_or_identity_query_parameters(
    query: str,
) -> None:
    engine_module = importlib.import_module("track_anywhere.infrastructure.db.engine")
    with pytest.raises(ValueError, match="query"):
        engine_module.create_v2_engine(
            f"postgresql+psycopg://user:password@database.example/ledger?{query}"
        )


def test_production_example_database_url_is_accepted() -> None:
    engine_module = importlib.import_module("track_anywhere.infrastructure.db.engine")
    environment = (REPOSITORY_ROOT / "deploy/env/prod.env.example").read_text(
        encoding="utf-8"
    )
    url = next(
        line.split("=", 1)[1]
        for line in environment.splitlines()
        if line.startswith("TRACK_ANYWHERE_DATABASE_URL=")
    )
    engine = engine_module.create_v2_engine(url)
    engine.dispose()


def test_require_postgres_17_accepts_the_disposable_cluster(
    empty_postgres_database,
) -> None:
    engine_module = importlib.import_module("track_anywhere.infrastructure.db.engine")
    engine = create_engine(empty_postgres_database.migrator_url)
    try:
        with engine.connect() as connection:
            assert engine_module.require_postgres_17(connection) in range(
                170000, 180000
            )
    finally:
        engine.dispose()


def test_empty_database_upgrades_to_the_only_v2_head(empty_postgres_database) -> None:
    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode == 0, result.stderr
    with create_engine(empty_postgres_database.runtime_url).connect() as connection:
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
    with create_engine(empty_postgres_database.admin_url).connect() as connection:
        owners = connection.execute(
            text(
                """
                select relation.relname, owner.rolname
                  from pg_catalog.pg_class relation
                  join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
                  join pg_catalog.pg_roles owner on owner.oid = relation.relowner
                 where namespace.nspname = 'public'
                   and relation.relname in ('alembic_version', 'v2_schema_metadata')
                 order by relation.relname
                """
            )
        ).all()
    assert [tuple(row) for row in owners] == [
        ("alembic_version", empty_postgres_database.owner_role),
        ("v2_schema_metadata", empty_postgres_database.owner_role),
    ]
    assert _relation_names(empty_postgres_database.migrator_url) == V2_RELATION_NAMES

    checked = _run_alembic(
        "check",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )
    assert checked.returncode == 0, checked.stderr
    assert "No new upgrade operations detected" in checked.stdout


@pytest.mark.parametrize(
    "ddl",
    (
        "create table public.unrelated_table(id integer)",
        "create table public.unrelated_partitioned(id integer) partition by range (id)",
        "create sequence public.unrelated_sequence",
        "create view public.unrelated_view as select 1 as value",
        "create materialized view public.unrelated_materialized_view as select 1 as value",
    ),
)
def test_first_revision_refuses_every_user_relation_and_rolls_back(
    empty_postgres_database,
    ddl: str,
) -> None:
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(text(ddl))
    relations_before = _relation_names(empty_postgres_database.migrator_url)
    acl_before = _public_acl_state(empty_postgres_database.migrator_url)

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "empty PostgreSQL database" in result.stderr
    assert _relation_names(empty_postgres_database.migrator_url) == relations_before
    assert _public_acl_state(empty_postgres_database.migrator_url) == acl_before


def test_first_revision_refuses_extra_user_schema_without_residue(
    empty_postgres_database,
) -> None:
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(text("create schema unrelated_schema"))
    acl_before = _public_acl_state(empty_postgres_database.migrator_url)

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "empty PostgreSQL database" in result.stderr
    assert _relation_names(empty_postgres_database.migrator_url) == []
    assert _public_acl_state(empty_postgres_database.migrator_url) == acl_before


def test_first_revision_refuses_a_foreign_table_if_postgres_fdw_is_available(
    empty_postgres_database,
) -> None:
    with create_engine(empty_postgres_database.admin_url).begin() as connection:
        connection.execute(text("create extension postgres_fdw"))
        connection.execute(
            text(
                "create server unrelated_server foreign data wrapper postgres_fdw "
                "options (host '127.0.0.1', dbname 'postgres')"
            )
        )
        connection.execute(
            text(
                "create foreign table public.unrelated_foreign_table(id integer) "
                "server unrelated_server"
            )
        )

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "empty PostgreSQL database" in result.stderr
    assert _relation_names(empty_postgres_database.migrator_url) == [
        "unrelated_foreign_table"
    ]


@pytest.mark.parametrize(
    ("ddl", "object_kind", "object_name"),
    (
        (
            "create type public.unrelated_enum as enum ('value')",
            "type",
            "unrelated_enum",
        ),
        ("create domain public.unrelated_domain as text", "type", "unrelated_domain"),
        (
            "create function public.unrelated_function() returns integer "
            "language sql immutable as 'select 1'",
            "function",
            "unrelated_function",
        ),
    ),
)
def test_first_revision_refuses_non_relation_schema_objects(
    empty_postgres_database,
    ddl: str,
    object_kind: str,
    object_name: str,
) -> None:
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(text(ddl))

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "empty PostgreSQL database" in result.stderr
    assert _relation_names(empty_postgres_database.migrator_url) == []
    assert _schema_object_exists(
        empty_postgres_database.migrator_url,
        object_kind,
        object_name,
    )


def test_first_revision_allows_only_a_verified_alembic_version_table(
    empty_postgres_database,
) -> None:
    _create_standard_version_table(empty_postgres_database)

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode == 0, result.stderr
    assert _relation_names(empty_postgres_database.migrator_url) == V2_RELATION_NAMES


@pytest.mark.parametrize(
    "mutation",
    (
        "alter table public.alembic_version set unlogged",
        "alter table public.alembic_version enable row level security",
        "alter table public.alembic_version force row level security",
        "create policy av_policy on public.alembic_version using (true)",
        "create trigger av_trigger before update on public.alembic_version "
        "for each row execute function pg_catalog.suppress_redundant_updates_trigger()",
        "create rule av_rule as on update to public.alembic_version do also nothing",
        "alter table public.alembic_version set (fillfactor = 80)",
        "alter table public.alembic_version add constraint av_extra_check "
        "check (length(version_num) > 0)",
        "create index av_extra_index on public.alembic_version(version_num)",
        "alter table public.alembic_version alter column version_num set default 'forged'",
        "alter table public.alembic_version add column forged_identity bigint "
        "generated always as identity",
    ),
)
def test_first_revision_rejects_modified_alembic_version_internals(
    empty_postgres_database,
    mutation: str,
) -> None:
    _create_standard_version_table(empty_postgres_database)
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(text(mutation))

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "empty PostgreSQL database" in result.stderr
    assert "v2_schema_metadata" not in _relation_names(
        empty_postgres_database.migrator_url
    )


def test_first_revision_rejects_a_spoofed_alembic_version_table(
    empty_postgres_database,
) -> None:
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(
            text("create table public.alembic_version(version_num text, extra text)")
        )

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert _relation_names(empty_postgres_database.migrator_url) == ["alembic_version"]


def test_first_revision_rejects_an_alembic_version_table_with_acl(
    empty_postgres_database,
) -> None:
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(
            text(
                "create table public.alembic_version "
                "(version_num varchar(32) not null, constraint alembic_version_pkc primary key (version_num))"
            )
        )
        connection.execute(
            text(
                f'grant select on public.alembic_version to "{empty_postgres_database.runtime_role}"'
            )
        )

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert _relation_names(empty_postgres_database.migrator_url) == ["alembic_version"]


def test_offline_migrations_fail_closed(empty_postgres_database) -> None:
    result = _run_alembic(
        "upgrade",
        "head",
        "--sql",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "online" in result.stderr.lower()
    assert _relation_names(empty_postgres_database.migrator_url) == []


def test_stamp_head_cannot_bypass_the_generation_marker(
    empty_postgres_database,
) -> None:
    result = _run_alembic(
        "stamp",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "generation marker" in result.stderr.lower()
    assert _relation_names(empty_postgres_database.migrator_url) == []


def test_programmatic_stamp_is_rejected_on_a_clean_database(
    empty_postgres_database,
) -> None:
    result = _run_programmatic_alembic(
        "stamp",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert _relation_names(empty_postgres_database.migrator_url) == []


def test_programmatic_stamp_cannot_accept_a_preforged_marker(
    empty_postgres_database,
) -> None:
    with create_engine(empty_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{empty_postgres_database.owner_role}"'))
        connection.execute(
            text(
                "create table public.v2_schema_metadata "
                "(schema_generation smallint primary key)"
            )
        )
        connection.execute(text("insert into public.v2_schema_metadata values (2)"))

    result = _run_programmatic_alembic(
        "stamp",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert _relation_names(empty_postgres_database.migrator_url) == [
        "v2_schema_metadata"
    ]


def test_programmatic_current_rejects_marker_without_version_table(
    migrated_postgres_database,
) -> None:
    with create_engine(migrated_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{migrated_postgres_database.owner_role}"'))
        connection.execute(text("drop table public.alembic_version"))

    result = _run_programmatic_alembic(
        "current",
        database_url=migrated_postgres_database.migrator_url,
        runtime_role=migrated_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert _relation_names(migrated_postgres_database.migrator_url) == sorted(
        {"ledger_global_sequence", *V2_MODEL_TABLES}
    )


def test_programmatic_current_accepts_a_valid_baseline(
    migrated_postgres_database,
) -> None:
    result = _run_programmatic_alembic(
        "current",
        database_url=migrated_postgres_database.migrator_url,
        runtime_role=migrated_postgres_database.runtime_role,
    )

    assert result.returncode == 0, result.stderr
    assert current_v2_head() in result.stdout


def test_stamp_base_cannot_detach_an_existing_generation_marker(
    migrated_postgres_database,
) -> None:
    result = _run_alembic(
        "stamp",
        "base",
        database_url=migrated_postgres_database.migrator_url,
        runtime_role=migrated_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "generation marker" in result.stderr.lower()
    with create_engine(migrated_postgres_database.runtime_url).connect() as connection:
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


def test_irreversible_downgrade_fails_without_changing_the_baseline(
    migrated_postgres_database,
) -> None:
    result = _run_alembic(
        "downgrade",
        "base",
        database_url=migrated_postgres_database.migrator_url,
        runtime_role=migrated_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    with create_engine(migrated_postgres_database.runtime_url).connect() as connection:
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


@pytest.mark.parametrize(
    ("url_present", "url", "role_present", "role", "message"),
    (
        (False, None, True, "track_anywhere_runtime", "database url"),
        (True, "", True, "track_anywhere_runtime", "database url"),
        (
            True,
            "not-a-url-containing-config-secret-sentinel",
            True,
            "track_anywhere_runtime",
            "database url",
        ),
        (True, "valid", False, None, "runtime role"),
        (True, "valid", True, None, "runtime role"),
        (True, "valid", True, "Unsafe-Role", "runtime role"),
        (True, "valid", True, "a" * 64, "runtime role"),
    ),
)
def test_alembic_requires_secret_safe_explicit_configuration(
    empty_postgres_database,
    url_present: bool,
    url: str | None,
    role_present: bool,
    role: str | None,
    message: str,
) -> None:
    actual_url = empty_postgres_database.migrator_url if url == "valid" else url
    result = _run_alembic(
        "upgrade",
        "head",
        database_url=actual_url,
        runtime_role=role,
        database_url_present=url_present,
        runtime_role_present=role_present,
    )

    assert result.returncode != 0
    assert message in result.stderr.lower()
    assert "track_anywhere_migrator_test" not in result.stderr
    assert "config-secret-sentinel" not in result.stderr


def test_environment_url_wins_over_explicit_config(
    postgres_database_factory,
    tmp_path: Path,
) -> None:
    configured = postgres_database_factory.create(purpose="configured")
    environment = postgres_database_factory.create(purpose="environment")
    config = _write_alembic_config(tmp_path, configured.migrator_url)

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=environment.migrator_url,
        runtime_role=environment.runtime_role,
        config_path=config,
    )

    assert result.returncode == 0, result.stderr
    assert _relation_names(configured.migrator_url) == []
    assert _relation_names(environment.migrator_url) == V2_RELATION_NAMES


def test_explicit_config_url_is_used_only_when_environment_key_is_absent(
    empty_postgres_database,
    tmp_path: Path,
) -> None:
    config = _write_alembic_config(tmp_path, empty_postgres_database.migrator_url)

    result = _run_alembic(
        "upgrade",
        "head",
        runtime_role=empty_postgres_database.runtime_role,
        config_path=config,
        database_url_present=False,
    )

    assert result.returncode == 0, result.stderr


def test_present_but_empty_environment_url_overrides_config_and_is_rejected(
    empty_postgres_database,
    tmp_path: Path,
) -> None:
    config = _write_alembic_config(tmp_path, empty_postgres_database.migrator_url)

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=None,
        runtime_role=empty_postgres_database.runtime_role,
        config_path=config,
    )

    assert result.returncode != 0
    assert _relation_names(empty_postgres_database.migrator_url) == []


@pytest.mark.parametrize("session_kind", ("admin", "runtime"))
def test_migration_refuses_admin_and_runtime_sessions(
    empty_postgres_database,
    session_kind: str,
) -> None:
    database_url = getattr(empty_postgres_database, f"{session_kind}_url")
    result = _run_alembic(
        "upgrade",
        "head",
        database_url=database_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "migrator" in result.stderr.lower()
    assert _relation_names(empty_postgres_database.migrator_url) == []


@pytest.mark.parametrize("runtime_kind", ("owner", "migrator"))
def test_runtime_role_must_be_distinct_from_database_roles(
    empty_postgres_database,
    runtime_kind: str,
) -> None:
    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=getattr(empty_postgres_database, f"{runtime_kind}_role"),
    )

    assert result.returncode != 0
    assert "distinct" in result.stderr.lower()


def test_migration_rejects_owner_upstream_membership(
    postgres_cluster_config,
) -> None:
    with _temporary_role_config(postgres_cluster_config) as (config, roles):
        factory = PostgresDatabaseFactory(
            config,
            worker_id="ownerclosure",
            test_uuid=uuid.uuid4().hex,
        )
        database = factory.create(purpose="owner_membership")
        admin_engine = create_engine(config.admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'alter role "{roles["extra"]}" createdb'))
                connection.execute(
                    text(f'grant "{roles["extra"]}" to "{roles["owner"]}"')
                )

            result = _run_alembic(
                "upgrade",
                "head",
                database_url=database.migrator_url,
                runtime_role=database.runtime_role,
            )

            assert result.returncode != 0
            assert "owner" in result.stderr.lower()
            assert "membership" in result.stderr.lower()
            assert _relation_names(database.migrator_url) == []
        finally:
            factory.close()
            admin_engine.dispose()


@pytest.mark.parametrize(
    "attack",
    ("database_create", "database_temporary", "schema_create"),
)
def test_migration_refuses_preexisting_runtime_ddl_grants_without_acl_drift(
    empty_postgres_database,
    attack: str,
) -> None:
    with create_engine(empty_postgres_database.admin_url).begin() as connection:
        if attack == "database_create":
            connection.execute(
                text(
                    f'grant create on database "{empty_postgres_database.database_name}" '
                    f'to "{empty_postgres_database.runtime_role}"'
                )
            )
        elif attack == "database_temporary":
            connection.execute(
                text(
                    f'grant temporary on database "{empty_postgres_database.database_name}" '
                    f'to "{empty_postgres_database.runtime_role}"'
                )
            )
        else:
            connection.execute(
                text(
                    f"grant create on schema public "
                    f'to "{empty_postgres_database.runtime_role}"'
                )
            )
    state_before = _runtime_ddl_state(
        empty_postgres_database,
        empty_postgres_database.runtime_role,
    )

    result = _run_alembic(
        "upgrade",
        "head",
        database_url=empty_postgres_database.migrator_url,
        runtime_role=empty_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "runtime" in result.stderr.lower()
    assert "privilege" in result.stderr.lower()
    assert _relation_names(empty_postgres_database.migrator_url) == []
    assert (
        _runtime_ddl_state(
            empty_postgres_database,
            empty_postgres_database.runtime_role,
        )
        == state_before
    )


def test_migration_rejects_runtime_role_membership(
    empty_postgres_database,
    postgres_cluster_config,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    runtime_role = f"ta_task6_runtime_{suffix}"
    extra_role = f"ta_task6_extra_{suffix}"
    admin_engine = create_engine(
        postgres_cluster_config.admin_url, isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'create role "{extra_role}" nologin'))
            connection.execute(
                text(
                    f'create role "{runtime_role}" login '
                    "nosuperuser nocreatedb nocreaterole noreplication "
                    "nobypassrls noinherit"
                )
            )
            connection.execute(text(f'grant "{extra_role}" to "{runtime_role}"'))
        result = _run_alembic(
            "upgrade",
            "head",
            database_url=empty_postgres_database.migrator_url,
            runtime_role=runtime_role,
        )
        assert result.returncode != 0
        assert "membership" in result.stderr.lower()
        assert _relation_names(empty_postgres_database.migrator_url) == []
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'revoke "{extra_role}" from "{runtime_role}"'))
            connection.execute(text(f'drop role "{runtime_role}"'))
            connection.execute(text(f'drop role "{extra_role}"'))
        admin_engine.dispose()


def test_runtime_receives_readiness_only_and_exact_future_dml_privileges(
    migrated_postgres_database,
) -> None:
    runtime = migrated_postgres_database.runtime_role
    with create_engine(migrated_postgres_database.runtime_url).connect() as connection:
        assert (
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == current_v2_head()
        )
        marker_privileges = {
            privilege: connection.execute(
                text(
                    "select has_table_privilege(:role, 'public.v2_schema_metadata', :privilege)"
                ),
                {"role": runtime, "privilege": privilege},
            ).scalar_one()
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
                "MAINTAIN",
            )
        }
        assert marker_privileges == {
            "SELECT": True,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
            "MAINTAIN": False,
        }
        assert connection.execute(
            text("select has_schema_privilege(:role, 'public', 'USAGE')"),
            {"role": runtime},
        ).scalar_one()
        assert not connection.execute(
            text("select has_schema_privilege(:role, 'public', 'CREATE')"),
            {"role": runtime},
        ).scalar_one()

    for statement in (
        "update v2_schema_metadata set schema_generation = 3",
        "drop table v2_schema_metadata",
        "create table forbidden(id integer)",
    ):
        with create_engine(
            migrated_postgres_database.runtime_url
        ).connect() as connection:
            with pytest.raises(
                ProgrammingError, match="permission denied|must be owner"
            ):
                connection.execute(text(statement))

    with create_engine(migrated_postgres_database.admin_url).connect() as connection:
        default_privileges = {
            (row.object_type, row.privilege_type)
            for row in connection.execute(
                text(
                    """
                    select defaults.defaclobjtype as object_type, privileges.privilege_type
                      from pg_catalog.pg_default_acl defaults
                      join pg_catalog.pg_roles owner on owner.oid = defaults.defaclrole
                      join pg_catalog.pg_namespace namespace on namespace.oid = defaults.defaclnamespace
                      cross join lateral pg_catalog.aclexplode(defaults.defaclacl) acl
                      join pg_catalog.pg_roles grantee on grantee.oid = acl.grantee
                      cross join lateral (
                        select acl.privilege_type::text as privilege_type
                      ) privileges
                     where owner.rolname = :owner
                       and grantee.rolname = :runtime
                       and namespace.nspname = 'public'
                    """
                ),
                {"owner": migrated_postgres_database.owner_role, "runtime": runtime},
            )
        }
    assert default_privileges == {
        ("r", "DELETE"),
        ("r", "INSERT"),
        ("r", "SELECT"),
        ("r", "UPDATE"),
        ("S", "SELECT"),
        ("S", "USAGE"),
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "grant insert on public.v2_schema_metadata to {runtime}",
        "grant update on public.alembic_version to {runtime}",
        "alter default privileges in schema public grant truncate on tables to {runtime}",
        "alter default privileges in schema public grant update on sequences to {runtime}",
        "alter default privileges grant execute on functions to public",
    ),
)
def test_valid_baseline_rejects_runtime_acl_drift(
    migrated_postgres_database,
    mutation: str,
) -> None:
    statement = mutation.format(runtime=f'"{migrated_postgres_database.runtime_role}"')
    with create_engine(migrated_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{migrated_postgres_database.owner_role}"'))
        connection.execute(text(statement))

    result = _run_programmatic_alembic(
        "current",
        database_url=migrated_postgres_database.migrator_url,
        runtime_role=migrated_postgres_database.runtime_role,
    )

    assert result.returncode != 0
    assert "runtime" in result.stderr.lower()
    assert "privilege" in result.stderr.lower()


def test_future_owner_functions_are_not_executable_by_public_or_runtime(
    migrated_postgres_database,
) -> None:
    with create_engine(migrated_postgres_database.migrator_url).begin() as connection:
        connection.execute(text(f'SET ROLE "{migrated_postgres_database.owner_role}"'))
        connection.execute(
            text(
                "create function public.future_private_function() returns integer "
                "language sql immutable as 'select 1'"
            )
        )
    with create_engine(migrated_postgres_database.admin_url).connect() as connection:
        assert not connection.execute(
            text(
                "select has_function_privilege(:role, "
                "'public.future_private_function()', 'EXECUTE')"
            ),
            {"role": migrated_postgres_database.runtime_role},
        ).scalar_one()


def test_alembic_ini_contains_no_default_database_url() -> None:
    line = next(
        line
        for line in ALEMBIC_INI.read_text(encoding="utf-8").splitlines()
        if line.startswith("sqlalchemy.url")
    )
    assert line == "sqlalchemy.url ="
    assert "localhost" not in line
