from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


POSTGRESQL_DRIVER = "postgresql+psycopg"
_SAFE_QUERY_VALUES = {
    "sslmode": frozenset(("require", "verify-ca", "verify-full")),
    "channel_binding": frozenset(("prefer", "require")),
}


def _validate_query_parameters(url: URL) -> None:
    for name, value in url.query.items():
        if not isinstance(value, str):
            raise ValueError("database URL contains an unsafe query parameter override")
        if name == "connect_timeout":
            if (
                not value.isascii()
                or not value.isdecimal()
                or not 1 <= int(value) <= 60
            ):
                raise ValueError(
                    "database URL contains an unsafe query parameter override"
                )
            continue
        if value not in _SAFE_QUERY_VALUES.get(name, ()):
            raise ValueError("database URL contains an unsafe query parameter override")


def _validated_postgres_url(value: str | URL) -> URL:
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError):
        raise ValueError(
            "database URL must be a valid explicit PostgreSQL URL"
        ) from None
    if url.drivername != POSTGRESQL_DRIVER:
        raise ValueError("database URL must use the exact postgresql+psycopg driver")
    _validate_query_parameters(url)
    if not url.username or not url.host or not url.database:
        raise ValueError("database URL must include a login, host, and database")
    return url


def create_v2_engine(database_url: str | URL, **options: Any) -> Engine:
    url = _validated_postgres_url(database_url)
    return create_engine(url, pool_pre_ping=True, **options)


def require_postgres_17(connection: Connection) -> int:
    version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    if not 170000 <= version < 180000:
        raise RuntimeError("Track Anywhere V2 requires PostgreSQL 17 exactly")
    return version


__all__ = ["POSTGRESQL_DRIVER", "create_v2_engine", "require_postgres_17"]
