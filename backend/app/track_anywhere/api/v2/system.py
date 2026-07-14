from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

from ...infrastructure.db.engine import POSTGRESQL_DRIVER, require_postgres_17


_ROLE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parents[5] / "alembic"
_READY = {
    "status": "ok",
    "api_version": "v2",
    "checks": {"database": "ok", "schema": "ok"},
}
_NOT_READY = {
    "status": "error",
    "api_version": "v2",
    "checks": {"database": "error", "schema": "error"},
}


class ReadinessCheckError(RuntimeError):
    pass


def current_alembic_head() -> str:
    config = Config()
    config.set_main_option("script_location", str(_ALEMBIC_SCRIPT_LOCATION))
    heads = tuple(ScriptDirectory.from_config(config).get_heads())
    if len(heads) != 1:
        raise ReadinessCheckError("V2 code must contain exactly one Alembic head")
    return heads[0]


def validate_runtime_identity(
    identity: Mapping[str, object] | None,
    expected_runtime_role: str,
) -> None:
    if identity is None:
        raise ReadinessCheckError("expected runtime role does not exist")
    if (
        identity["session_user"] != expected_runtime_role
        or identity["current_user"] != expected_runtime_role
    ):
        raise ReadinessCheckError("database session is not the runtime identity")
    if identity["owner_role"] == expected_runtime_role:
        raise ReadinessCheckError("runtime identity must not own the database")
    if not bool(identity["rolcanlogin"]) or any(
        bool(identity[name])
        for name in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
            "rolbypassrls",
            "rolinherit",
        )
    ):
        raise ReadinessCheckError("runtime identity has unsafe role attributes")
    if int(identity["direct_memberships"]) != 0:
        raise ReadinessCheckError("runtime identity has direct role membership")


def check_v2_readiness(
    *,
    engine: Engine | None,
    expected_runtime_role: str | None,
) -> None:
    if engine is None:
        raise ReadinessCheckError("runtime database is not configured")
    if engine.url.drivername != POSTGRESQL_DRIVER:
        raise ReadinessCheckError("runtime database driver is invalid")
    if (
        type(expected_runtime_role) is not str
        or not _ROLE_IDENTIFIER.fullmatch(expected_runtime_role)
        or len(expected_runtime_role.encode("ascii")) > 63
    ):
        raise ReadinessCheckError("expected runtime role is invalid")
    code_head = current_alembic_head()
    with engine.connect() as connection:
        require_postgres_17(connection)
        identity = (
            connection.execute(
                text(
                    """
                select session_user::text as session_user,
                       current_user::text as current_user,
                       owner.rolname::text as owner_role,
                       runtime.rolcanlogin,
                       runtime.rolsuper,
                       runtime.rolcreatedb,
                       runtime.rolcreaterole,
                       runtime.rolreplication,
                       runtime.rolbypassrls,
                       runtime.rolinherit,
                       (
                           select count(*)
                             from pg_catalog.pg_auth_members membership
                            where membership.member = runtime.oid
                       ) as direct_memberships
                  from pg_catalog.pg_database database
                  join pg_catalog.pg_roles owner
                    on owner.oid = database.datdba
                  join pg_catalog.pg_roles runtime
                    on runtime.rolname = :expected_runtime_role
                 where database.datname = current_database()
                """
                ),
                {"expected_runtime_role": expected_runtime_role},
            )
            .mappings()
            .one_or_none()
        )
        validate_runtime_identity(identity, expected_runtime_role)

        database_heads = tuple(
            connection.execute(
                text("select version_num from alembic_version")
            ).scalars()
        )
        if database_heads != (code_head,):
            raise ReadinessCheckError("database Alembic head is not exact")

        schema_generations = tuple(
            connection.execute(
                text("select schema_generation from v2_schema_metadata")
            ).scalars()
        )
        if schema_generations != (2,):
            raise ReadinessCheckError("database schema generation is not exact")


def create_system_router(
    *,
    engine: Engine | None,
    expected_runtime_role: str | None,
) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "api_version": "v2"}

    @router.get("/ready")
    def ready() -> JSONResponse:
        try:
            check_v2_readiness(
                engine=engine,
                expected_runtime_role=expected_runtime_role,
            )
        except Exception:
            return JSONResponse(status_code=503, content=_NOT_READY)
        return JSONResponse(status_code=200, content=_READY)

    return router


__all__ = [
    "ReadinessCheckError",
    "check_v2_readiness",
    "create_system_router",
    "current_alembic_head",
    "validate_runtime_identity",
]
