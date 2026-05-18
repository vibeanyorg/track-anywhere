from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import StaticPool


DEFAULT_DATABASE_URL = "sqlite:///./.local/track-anywhere.sqlite3"


def database_url_from_env() -> str:
    return os.getenv("TRACK_ANYWHERE_DATABASE_URL", DEFAULT_DATABASE_URL)


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername.split("+", 1)[0] != "sqlite":
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_database_engine(database_url: str) -> Engine:
    _ensure_sqlite_parent(database_url)
    url = make_url(database_url)
    is_sqlite = url.drivername.split("+", 1)[0] == "sqlite"
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    kwargs: dict[str, Any] = {"connect_args": connect_args, "future": True}
    if is_sqlite and url.database == ":memory:":
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)
