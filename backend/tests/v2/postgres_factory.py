from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


ADMIN_URL_ENV = "TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL"
MIGRATOR_URL_ENV = "TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL"
RUNTIME_URL_ENV = "TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL"
EXTERNAL_DATABASE_GATE_ENV = "TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE"

_ROLE_ENVIRONMENTS = {
    "owner": "TRACK_ANYWHERE_OWNER_ROLE",
    "migrator": "TRACK_ANYWHERE_MIGRATOR_ROLE",
    "runtime": "TRACK_ANYWHERE_RUNTIME_ROLE",
}
_DEFAULT_ROLES = {
    "owner": "track_anywhere_owner",
    "migrator": "track_anywhere_migrator",
    "runtime": "track_anywhere_runtime",
}
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_IDENTIFIER_COMPONENT = re.compile(r"^[a-z0-9_]+$")
_DATABASE_PREFIX = "ta_v2_"
_POSTGRES_DRIVER = "postgresql+psycopg"


def _render_url(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _required_url(environment: Mapping[str, str], name: str) -> URL:
    value = environment.get(name)
    if not value:
        raise ValueError(f"missing required PostgreSQL 17 test URL: {name}")
    return _parse_postgres_url(value, label=name)


def _parse_postgres_url(value: str, *, label: str) -> URL:
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a valid PostgreSQL 17 test URL") from error
    if url.drivername != _POSTGRES_DRIVER:
        raise ValueError(f"{label} must use the exact postgresql+psycopg driver")
    if url.query:
        raise ValueError(f"{label} must not contain query parameters")
    if not url.username:
        raise ValueError(f"{label} must include a login identity")
    if url.password is None:
        raise ValueError(f"{label} must include a disposable test password")
    if not url.database:
        raise ValueError(f"{label} must include a database path")
    return url


def _validate_identifier(value: str, *, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase PostgreSQL identifier")
    if len(value.encode("ascii")) > 63:
        raise ValueError(f"{label} exceeds PostgreSQL's 63-byte identifier limit")
    return value


def _quoted_identifier(value: str) -> str:
    _validate_identifier(value, label="PostgreSQL identifier")
    return f'"{value}"'


def _validate_identifier_component(value: str, *, label: str) -> str:
    if not _IDENTIFIER_COMPONENT.fullmatch(value):
        raise ValueError(f"{label} must contain only lowercase letters, digits, and underscores")
    return value


def _cluster_address(url: URL) -> tuple[str, int]:
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("PostgreSQL 17 test URLs must use a loopback host")
    return url.host, url.port or 5432


@dataclass(frozen=True)
class ClusterConfig:
    admin_base_url: URL
    migrator_base_url: URL
    runtime_base_url: URL
    owner_role: str
    migrator_role: str
    runtime_role: str

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> ClusterConfig:
        values = os.environ if environment is None else environment
        if values.get(EXTERNAL_DATABASE_GATE_ENV) != "1":
            raise ValueError(f"{EXTERNAL_DATABASE_GATE_ENV}=1 is required for the PostgreSQL 17 test lane")

        admin = _required_url(values, ADMIN_URL_ENV)
        migrator = _required_url(values, MIGRATOR_URL_ENV)
        runtime = _required_url(values, RUNTIME_URL_ENV)

        addresses = {_cluster_address(url) for url in (admin, migrator, runtime)}
        if len(addresses) != 1:
            raise ValueError("admin, migrator, and runtime URLs must use the same loopback host and port")
        if len({admin.database, migrator.database, runtime.database}) != 1:
            raise ValueError("admin, migrator, and runtime URLs must use the same base database path")

        identities = {admin.username, migrator.username, runtime.username}
        if len(identities) != 3:
            raise ValueError("admin, migrator, and runtime URLs must use three distinct login identities")

        owner_role = _validate_identifier(
            values.get(_ROLE_ENVIRONMENTS["owner"], _DEFAULT_ROLES["owner"]),
            label="owner role",
        )
        migrator_role = _validate_identifier(
            values.get(_ROLE_ENVIRONMENTS["migrator"], migrator.username),
            label="migrator role",
        )
        runtime_role = _validate_identifier(
            values.get(_ROLE_ENVIRONMENTS["runtime"], runtime.username),
            label="runtime role",
        )
        if migrator_role != migrator.username:
            raise ValueError("migrator URL login must match TRACK_ANYWHERE_MIGRATOR_ROLE")
        if runtime_role != runtime.username:
            raise ValueError("runtime URL login must match TRACK_ANYWHERE_RUNTIME_ROLE")
        if len({owner_role, migrator_role, runtime_role, admin.username}) != 4:
            raise ValueError("owner, admin, migrator, and runtime identities must be distinct")

        return cls(
            admin_base_url=admin,
            migrator_base_url=migrator,
            runtime_base_url=runtime,
            owner_role=owner_role,
            migrator_role=migrator_role,
            runtime_role=runtime_role,
        )

    def child_url(self, base_url: URL, database_name: str) -> str:
        return _render_url(base_url.set(database=database_name))

    @property
    def admin_url(self) -> str:
        return _render_url(self.admin_base_url)

    def database_name_from_drop_url(self, value: str) -> str:
        url = _parse_postgres_url(value, label="drop URL")
        if _cluster_address(url) != _cluster_address(self.admin_base_url):
            raise ValueError("drop URL must use the configured loopback PostgreSQL cluster")
        for base_url in (self.migrator_base_url, self.runtime_base_url):
            if url.set(database=base_url.database) == base_url:
                database_name = url.database
                break
        else:
            raise ValueError("drop URL must use the configured migrator or runtime identity")
        if not database_name.startswith(_DATABASE_PREFIX):
            raise ValueError(f"drop URL must name a database in the {_DATABASE_PREFIX} test namespace")
        _validate_identifier(database_name, label="drop database name")
        return database_name


@dataclass(frozen=True)
class ProvisionedDatabase:
    database_name: str
    owner_role: str
    migrator_role: str
    runtime_role: str
    admin_url: str
    migrator_url: str
    runtime_url: str


class PostgresDatabaseFactory:
    def __init__(self, config: ClusterConfig, *, worker_id: str, test_uuid: str) -> None:
        self.config = config
        self.worker_id = _validate_identifier_component(_slug(worker_id), label="pytest worker id")
        self.test_uuid = _validate_identifier_component(_slug(test_uuid), label="test UUID")[:16]
        self._created: dict[str, ProvisionedDatabase] = {}
        self._counter = 0

    @staticmethod
    def default_role_name(kind: str) -> str:
        try:
            return _DEFAULT_ROLES[kind]
        except KeyError as exc:
            raise ValueError(f"unknown PostgreSQL role kind: {kind}") from exc

    def role_name(self, kind: str) -> str:
        try:
            return {
                "owner": self.config.owner_role,
                "migrator": self.config.migrator_role,
                "runtime": self.config.runtime_role,
            }[kind]
        except KeyError as exc:
            raise ValueError(f"unknown PostgreSQL role kind: {kind}") from exc

    def create(self, *, purpose: str, schema: str = "empty") -> ProvisionedDatabase:
        if schema != "empty":
            raise ValueError("Task 1 database factory supports only --schema empty")

        self._counter += 1
        purpose_slug = _slug(purpose)[:18]
        database_name = _validate_identifier(
            f"{_DATABASE_PREFIX}{self.worker_id[:10]}_{self.test_uuid}_{purpose_slug}_{self._counter}",
            label="test database name",
        )
        if len(database_name) > 63:
            raise ValueError("generated test database name exceeds PostgreSQL's 63-byte limit")

        self._verify_cluster_roles()
        handle = ProvisionedDatabase(
            database_name=database_name,
            owner_role=self.config.owner_role,
            migrator_role=self.config.migrator_role,
            runtime_role=self.config.runtime_role,
            admin_url=self.config.child_url(self.config.admin_base_url, database_name),
            migrator_url=self.config.child_url(self.config.migrator_base_url, database_name),
            runtime_url=self.config.child_url(self.config.runtime_base_url, database_name),
        )

        database_created = False
        engine = create_engine(self.config.admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    f"CREATE DATABASE {_quoted_identifier(database_name)} "
                    f"OWNER {_quoted_identifier(self.config.owner_role)} TEMPLATE template0 ENCODING 'UTF8'"
                )
                database_created = True
                connection.exec_driver_sql(
                    f"REVOKE ALL PRIVILEGES ON DATABASE {_quoted_identifier(database_name)} FROM PUBLIC"
                )
                connection.exec_driver_sql(
                    f"GRANT CONNECT ON DATABASE {_quoted_identifier(database_name)} "
                    f"TO {_quoted_identifier(self.config.migrator_role)}"
                )
                connection.exec_driver_sql(
                    f"GRANT CONNECT ON DATABASE {_quoted_identifier(database_name)} "
                    f"TO {_quoted_identifier(self.config.runtime_role)}"
                )
        except BaseException:
            if database_created:
                self._drop_database(database_name)
            raise
        finally:
            engine.dispose()

        self._created[database_name] = handle
        return handle

    def drop(self, database: ProvisionedDatabase | str) -> None:
        database_name = database.database_name if isinstance(database, ProvisionedDatabase) else database
        if not database_name.startswith(_DATABASE_PREFIX):
            raise ValueError(f"refusing to drop a database outside the {_DATABASE_PREFIX} test namespace")
        self._drop_database(database_name)
        self._created.pop(database_name, None)

    def close(self) -> None:
        for database_name in reversed(tuple(self._created)):
            self._drop_database(database_name)
            self._created.pop(database_name, None)

    def _verify_cluster_roles(self) -> None:
        engine = create_engine(self.config.admin_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                rows = {
                    row.rolname: row
                    for row in connection.execute(
                        text(
                            """
                            select rolname, rolsuper, rolcreatedb, rolcreaterole,
                                   rolreplication, rolbypassrls, rolinherit, rolcanlogin
                              from pg_roles
                             where rolname in (:owner, :migrator, :runtime)
                            """
                        ),
                        {
                            "owner": self.config.owner_role,
                            "migrator": self.config.migrator_role,
                            "runtime": self.config.runtime_role,
                        },
                    )
                }
                missing = {
                    self.config.owner_role,
                    self.config.migrator_role,
                    self.config.runtime_role,
                } - rows.keys()
                if missing:
                    raise RuntimeError(f"required V2 PostgreSQL role(s) missing: {', '.join(sorted(missing))}")

                owner = rows[self.config.owner_role]
                migrator = rows[self.config.migrator_role]
                runtime = rows[self.config.runtime_role]
                forbidden_attributes = (
                    "rolsuper",
                    "rolcreatedb",
                    "rolcreaterole",
                    "rolreplication",
                    "rolbypassrls",
                    "rolinherit",
                )
                if owner.rolcanlogin or any(getattr(owner, attribute) for attribute in forbidden_attributes):
                    raise RuntimeError(
                        "V2 owner role must be NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS NOINHERIT"
                    )
                for name, role in (("migrator", migrator), ("runtime", runtime)):
                    if not role.rolcanlogin or any(
                        getattr(role, attribute) for attribute in forbidden_attributes
                    ):
                        raise RuntimeError(
                            f"V2 {name} role must be LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOREPLICATION NOBYPASSRLS NOINHERIT"
                        )

                memberships = connection.execute(
                    text(
                        """
                        select member.rolname as member_role,
                               granted.rolname as granted_role,
                               membership.admin_option,
                               membership.inherit_option,
                               membership.set_option
                          from pg_auth_members membership
                          join pg_roles member on member.oid = membership.member
                          join pg_roles granted on granted.oid = membership.roleid
                         where member.rolname in (:migrator, :runtime)
                         order by member.rolname, granted.rolname
                        """
                    ),
                    {
                        "migrator": self.config.migrator_role,
                        "runtime": self.config.runtime_role,
                    },
                ).mappings().all()
                runtime_memberships = [
                    membership
                    for membership in memberships
                    if membership["member_role"] == self.config.runtime_role
                ]
                if runtime_memberships:
                    raise RuntimeError("V2 runtime role must not have any direct role membership")

                migrator_memberships = [
                    membership
                    for membership in memberships
                    if membership["member_role"] == self.config.migrator_role
                ]
                expected_membership = {
                    "member_role": self.config.migrator_role,
                    "granted_role": self.config.owner_role,
                    "admin_option": False,
                    "inherit_option": False,
                    "set_option": True,
                }
                if len(migrator_memberships) != 1 or dict(migrator_memberships[0]) != expected_membership:
                    raise RuntimeError(
                        "V2 migrator role must have only owner membership with "
                        "ADMIN FALSE, INHERIT FALSE, SET TRUE"
                    )
        finally:
            engine.dispose()

    def _drop_database(self, database_name: str) -> None:
        engine = create_engine(self.config.admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    f"DROP DATABASE IF EXISTS {_quoted_identifier(database_name)} WITH (FORCE)"
                )
        finally:
            engine.dispose()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "test"


def _factory_from_environment() -> PostgresDatabaseFactory:
    return PostgresDatabaseFactory(
        ClusterConfig.from_env(),
        worker_id=os.environ.get("PYTEST_XDIST_WORKER", "cli"),
        test_uuid=uuid.uuid4().hex,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provision disposable Track Anywhere V2 test databases")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--purpose", required=True)
    create.add_argument("--schema", required=True, choices=("empty",))
    create.add_argument("--emit-role", required=True, choices=("migrator", "runtime"))

    role_name = commands.add_parser("role-name")
    role_name.add_argument("--kind", required=True, choices=("owner", "migrator", "runtime"))

    drop = commands.add_parser("drop")
    drop.add_argument("--url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    factory = _factory_from_environment()
    if arguments.command == "role-name":
        print(factory.role_name(arguments.kind))
        return 0
    if arguments.command == "create":
        database = factory.create(purpose=arguments.purpose, schema=arguments.schema)
        print(database.migrator_url if arguments.emit_role == "migrator" else database.runtime_url)
        return 0

    database_name = factory.config.database_name_from_drop_url(arguments.url)
    factory.drop(database_name)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
