from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path
import re
import sys

from alembic import context
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Connection, Engine, text
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "backend" / "app"
if str(APP_PATH) not in sys.path:
    sys.path.insert(0, str(APP_PATH))

from track_anywhere.infrastructure.db.base import V2Base, load_v2_models  # noqa: E402
from track_anywhere.infrastructure.db.engine import (  # noqa: E402
    create_v2_engine,
    require_postgres_17,
)


DATABASE_URL_ENV = "TRACK_ANYWHERE_DATABASE_URL"
RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
_V2_BASELINE_REVISION = "v2_0001_schema_guard"

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

load_v2_models()
target_metadata = V2Base.metadata


def _required_database_url() -> str:
    if DATABASE_URL_ENV in os.environ:
        value = os.environ[DATABASE_URL_ENV]
    else:
        value = config.get_main_option("sqlalchemy.url")
    if not value or not value.strip():
        raise RuntimeError("an explicit PostgreSQL database URL is required")
    return value


def _required_runtime_role() -> str:
    if RUNTIME_ROLE_ENV not in os.environ or not os.environ[RUNTIME_ROLE_ENV].strip():
        raise RuntimeError(
            "database runtime role TRACK_ANYWHERE_DB_RUNTIME_ROLE is required"
        )
    role = os.environ[RUNTIME_ROLE_ENV]
    if (
        not _IDENTIFIER_PATTERN.fullmatch(role)
        or len(role.encode("ascii", errors="ignore")) > 63
    ):
        raise RuntimeError(
            "database runtime role must be a safe lowercase PostgreSQL identifier"
        )
    return role


def _role_rows(connection: Connection, names: set[str]) -> dict[str, object]:
    rows = connection.execute(
        text(
            """
            select rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls, rolinherit
              from pg_catalog.pg_roles
             where rolname = any(:names)
            """
        ),
        {"names": sorted(names)},
    ).mappings()
    return {str(row["rolname"]): row for row in rows}


def _has_safe_role_flags(role: object, *, login: bool) -> bool:
    return bool(role["rolcanlogin"]) is login and not any(
        bool(role[attribute])
        for attribute in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
            "rolbypassrls",
            "rolinherit",
        )
    )


def _validate_migration_identity(connection: Connection, runtime_role: str) -> str:
    identity = (
        connection.execute(
            text(
                """
            select session_user::text as session_user,
                   current_user::text as current_user,
                   owner.rolname::text as owner_role
              from pg_catalog.pg_database database
              join pg_catalog.pg_roles owner on owner.oid = database.datdba
             where database.datname = current_database()
            """
            )
        )
        .mappings()
        .one()
    )
    session_user = str(identity["session_user"])
    owner_role = str(identity["owner_role"])
    if identity["current_user"] != session_user:
        raise RuntimeError("migration connection must begin without an active SET ROLE")
    if len({session_user, owner_role, runtime_role}) != 3:
        raise RuntimeError(
            "database owner, migrator, and runtime roles must be distinct"
        )
    for role_name, label in (
        (session_user, "migrator"),
        (owner_role, "owner"),
        (runtime_role, "runtime"),
    ):
        if (
            not _IDENTIFIER_PATTERN.fullmatch(role_name)
            or len(role_name.encode("ascii")) > 63
        ):
            raise RuntimeError(
                f"database {label} role must be a safe lowercase identifier"
            )

    roles = _role_rows(connection, {session_user, owner_role, runtime_role})
    if set(roles) != {session_user, owner_role, runtime_role}:
        raise RuntimeError(
            "required database owner, migrator, or runtime role does not exist"
        )
    if not _has_safe_role_flags(roles[owner_role], login=False):
        raise RuntimeError("database owner role has unsafe attributes")
    if not _has_safe_role_flags(roles[runtime_role], login=True):
        raise RuntimeError("database runtime role has unsafe attributes")
    if not _has_safe_role_flags(roles[session_user], login=True):
        raise RuntimeError(
            "migration session must use the dedicated safe migrator role"
        )

    memberships = (
        connection.execute(
            text(
                """
            select member.rolname::text as member_role,
                   granted.rolname::text as granted_role,
                   membership.admin_option,
                   membership.inherit_option,
                   membership.set_option
              from pg_catalog.pg_auth_members membership
              join pg_catalog.pg_roles member on member.oid = membership.member
              join pg_catalog.pg_roles granted on granted.oid = membership.roleid
             where member.rolname = any(:members)
             order by member.rolname, granted.rolname
            """
            ),
            {"members": [session_user, owner_role, runtime_role]},
        )
        .mappings()
        .all()
    )
    runtime_memberships = [
        row for row in memberships if row["member_role"] == runtime_role
    ]
    if runtime_memberships:
        raise RuntimeError(
            "database runtime role must not have any direct role membership"
        )
    owner_memberships = [row for row in memberships if row["member_role"] == owner_role]
    if owner_memberships:
        raise RuntimeError(
            "database owner role must not have any direct role membership"
        )
    migrator_memberships = [
        row for row in memberships if row["member_role"] == session_user
    ]
    expected = {
        "member_role": session_user,
        "granted_role": owner_role,
        "admin_option": False,
        "inherit_option": False,
        "set_option": True,
    }
    if len(migrator_memberships) != 1 or dict(migrator_memberships[0]) != expected:
        raise RuntimeError(
            "migration session must use a migrator with only owner membership "
            "and ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )
    return owner_role


def _validate_runtime_ddl_boundary(connection: Connection, runtime_role: str) -> None:
    database_privileges = tuple(
        connection.execute(
            text(
                """
                select pg_catalog.has_database_privilege(
                           :runtime_role, current_database(), 'CONNECT'
                       ),
                       pg_catalog.has_database_privilege(
                           :runtime_role, current_database(), 'CREATE'
                       ),
                       pg_catalog.has_database_privilege(
                           :runtime_role, current_database(), 'TEMPORARY'
                       )
                """
            ),
            {"runtime_role": runtime_role},
        ).one()
    )
    schema_create = connection.execute(
        text(
            "select pg_catalog.has_schema_privilege(:runtime_role, 'public', 'CREATE')"
        ),
        {"runtime_role": runtime_role},
    ).scalar_one()
    if database_privileges != (True, False, False) or bool(schema_create):
        raise RuntimeError("database runtime privilege matrix permits unsafe DDL")


def _quote_identifier(identifier: str) -> str:
    if (
        not _IDENTIFIER_PATTERN.fullmatch(identifier)
        or len(identifier.encode("ascii")) > 63
    ):
        raise RuntimeError("unsafe PostgreSQL identifier")
    return f'"{identifier}"'


def _validate_runtime_privilege_boundary(
    connection: Connection,
    *,
    owner_role: str,
    runtime_role: str,
) -> None:
    _validate_runtime_ddl_boundary(connection, runtime_role)

    schema_privileges = tuple(
        connection.execute(
            text(
                """
                select pg_catalog.has_schema_privilege(
                           :runtime_role, 'public', 'USAGE'
                       ),
                       pg_catalog.has_schema_privilege(
                           :runtime_role, 'public', 'CREATE'
                       )
                """
            ),
            {"runtime_role": runtime_role},
        ).one()
    )
    if schema_privileges != (True, False):
        raise RuntimeError(
            "database runtime privilege matrix has unsafe schema privileges"
        )

    table_privileges = [
        tuple(row)
        for row in connection.execute(
            text(
                """
                select requested.relation_name,
                       requested.privilege_name,
                       pg_catalog.has_table_privilege(
                           :runtime_role,
                           'public.' || requested.relation_name,
                           requested.privilege_name
                       ) as allowed
                  from (
                        values
                          ('alembic_version', 'SELECT'),
                          ('alembic_version', 'INSERT'),
                          ('alembic_version', 'UPDATE'),
                          ('alembic_version', 'DELETE'),
                          ('alembic_version', 'TRUNCATE'),
                          ('alembic_version', 'REFERENCES'),
                          ('alembic_version', 'TRIGGER'),
                          ('alembic_version', 'MAINTAIN'),
                          ('v2_schema_metadata', 'SELECT'),
                          ('v2_schema_metadata', 'INSERT'),
                          ('v2_schema_metadata', 'UPDATE'),
                          ('v2_schema_metadata', 'DELETE'),
                          ('v2_schema_metadata', 'TRUNCATE'),
                          ('v2_schema_metadata', 'REFERENCES'),
                          ('v2_schema_metadata', 'TRIGGER'),
                          ('v2_schema_metadata', 'MAINTAIN')
                       ) requested(relation_name, privilege_name)
                 order by requested.relation_name, requested.privilege_name
                """
            ),
            {"runtime_role": runtime_role},
        )
    ]
    expected_table_privileges = [
        (relation_name, privilege_name, privilege_name == "SELECT")
        for relation_name in ("alembic_version", "v2_schema_metadata")
        for privilege_name in (
            "DELETE",
            "INSERT",
            "MAINTAIN",
            "REFERENCES",
            "SELECT",
            "TRIGGER",
            "TRUNCATE",
            "UPDATE",
        )
    ]
    if table_privileges != expected_table_privileges:
        raise RuntimeError(
            "database runtime privilege matrix has unsafe table privileges"
        )

    direct_table_privileges = [
        tuple(row)
        for row in connection.execute(
            text(
                """
                select relation.relname::text,
                       coalesce(grantee.rolname::text, 'PUBLIC'),
                       acl.privilege_type::text,
                       acl.is_grantable
                  from pg_catalog.pg_class relation
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = relation.relnamespace
                  cross join lateral pg_catalog.aclexplode(relation.relacl) acl
                  left join pg_catalog.pg_roles grantee
                    on grantee.oid = acl.grantee
                 where namespace.nspname = 'public'
                   and relation.relname in (
                       'alembic_version', 'v2_schema_metadata'
                   )
                   and (acl.grantee = 0 or grantee.rolname = :runtime_role)
                 order by relation.relname,
                          coalesce(grantee.rolname::text, 'PUBLIC'),
                          acl.privilege_type
                """
            ),
            {"runtime_role": runtime_role},
        )
    ]
    if direct_table_privileges != [
        ("alembic_version", runtime_role, "SELECT", False),
        ("v2_schema_metadata", runtime_role, "SELECT", False),
    ]:
        raise RuntimeError(
            "database runtime privilege matrix has unsafe direct table ACLs"
        )

    default_privileges = [
        tuple(row)
        for row in connection.execute(
            text(
                """
                select defaults.defaclobjtype::text,
                       namespace.nspname::text,
                       default_owner.rolname::text,
                       coalesce(grantee.rolname::text, 'PUBLIC'),
                       acl.privilege_type::text,
                       acl.is_grantable
                  from pg_catalog.pg_default_acl defaults
                  join pg_catalog.pg_roles default_owner
                    on default_owner.oid = defaults.defaclrole
                  left join pg_catalog.pg_namespace namespace
                    on namespace.oid = defaults.defaclnamespace
                  cross join lateral pg_catalog.aclexplode(defaults.defaclacl) acl
                  left join pg_catalog.pg_roles grantee
                    on grantee.oid = acl.grantee
                 order by defaults.defaclobjtype,
                          namespace.nspname nulls first,
                          default_owner.rolname,
                          coalesce(grantee.rolname::text, 'PUBLIC'),
                          acl.privilege_type
                """
            )
        )
    ]
    expected_default_privileges = [
        ("S", "public", owner_role, runtime_role, "SELECT", False),
        ("S", "public", owner_role, runtime_role, "USAGE", False),
        ("f", None, owner_role, owner_role, "EXECUTE", False),
        ("r", "public", owner_role, runtime_role, "DELETE", False),
        ("r", "public", owner_role, runtime_role, "INSERT", False),
        ("r", "public", owner_role, runtime_role, "SELECT", False),
        ("r", "public", owner_role, runtime_role, "UPDATE", False),
    ]
    if default_privileges != expected_default_privileges:
        raise RuntimeError(
            "database runtime privilege matrix has unsafe default privileges"
        )


def _reject_stamp_operation() -> None:
    migration_function = context.get_context().opts.get("fn")
    if getattr(migration_function, "__name__", None) == "do_stamp":
        raise RuntimeError(
            "Alembic stamp cannot bypass or detach the V2 schema generation marker"
        )


def _run_in_outer_transaction(engine: Engine, runtime_role: str) -> None:
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
            require_postgres_17(connection)
            owner_role = _validate_migration_identity(connection, runtime_role)
            _validate_runtime_ddl_boundary(connection, runtime_role)
            connection.exec_driver_sql(
                f"SET LOCAL ROLE {_quote_identifier(owner_role)}"
            )
            connection.exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transactional_ddl=True,
                transaction_per_migration=False,
            )
            _reject_stamp_operation()
            with context.begin_transaction():
                context.run_migrations()
            if _assert_generation_marker_consistency(connection):
                _validate_runtime_privilege_boundary(
                    connection,
                    owner_role=owner_role,
                    runtime_role=runtime_role,
                )


def _revision_is_in_v2_lineage(revision_id: str) -> bool:
    script = ScriptDirectory.from_config(config)
    pending = [revision_id]
    visited: set[str] = set()
    while pending:
        candidate = pending.pop()
        if candidate in visited:
            continue
        visited.add(candidate)
        if candidate == _V2_BASELINE_REVISION:
            return True
        try:
            revision = script.get_revision(candidate)
        except CommandError:
            return False
        if revision is None:
            return False
        down_revision = revision.down_revision
        if isinstance(down_revision, str):
            pending.append(down_revision)
        elif down_revision is not None:
            pending.extend(str(parent) for parent in down_revision)
    return False


def _assert_generation_marker_consistency(connection: Connection) -> bool:
    version_table_exists = connection.execute(
        text("select pg_catalog.to_regclass('public.alembic_version') is not null")
    ).scalar_one()
    marker_exists = connection.execute(
        text("select pg_catalog.to_regclass('public.v2_schema_metadata') is not null")
    ).scalar_one()
    if not version_table_exists and not marker_exists:
        return False
    if not version_table_exists:
        raise RuntimeError(
            "V2 revision and schema generation marker must remain consistent"
        )

    revisions = (
        connection.execute(text("select version_num from public.alembic_version"))
        .scalars()
        .all()
    )
    generations = []
    if marker_exists:
        generations = (
            connection.execute(
                text("select schema_generation from public.v2_schema_metadata")
            )
            .scalars()
            .all()
        )
    if (
        len(revisions) == 1
        and generations == [2]
        and _revision_is_in_v2_lineage(str(revisions[0]))
    ):
        return True
    if not revisions and not marker_exists:
        return False
    raise RuntimeError(
        "V2 revision and schema generation marker must remain consistent"
    )


def _reject_stamp_command() -> None:
    command = getattr(getattr(config, "cmd_opts", None), "cmd", None)
    if command and getattr(command[0], "__name__", None) == "stamp":
        raise RuntimeError(
            "Alembic stamp cannot bypass or detach the V2 schema generation marker"
        )


def run_migrations_online() -> None:
    _reject_stamp_command()
    database_url = _required_database_url()
    runtime_role = _required_runtime_role()
    engine = create_v2_engine(database_url, poolclass=NullPool)
    try:
        _run_in_outer_transaction(engine, runtime_role)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Track Anywhere V2 migrations are online-only")
run_migrations_online()
