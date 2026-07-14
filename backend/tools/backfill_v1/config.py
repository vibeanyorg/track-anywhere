from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


_POSTGRES_DRIVER = "postgresql+psycopg"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _postgres_url(value: str, *, label: str) -> URL:
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a valid PostgreSQL URL") from error
    if url.drivername != _POSTGRES_DRIVER:
        raise ValueError(f"{label} must use the exact postgresql+psycopg driver")
    if not url.host or not url.database:
        raise ValueError(f"{label} must include a host and database")
    return url


def database_identity(value: str) -> tuple[str, int, str]:
    """Return a credential-independent physical database identity."""

    url = _postgres_url(value, label="database URL")
    host = url.host.casefold()
    if host in _LOOPBACK_HOSTS:
        host = "loopback"
    return host, url.port or 5432, url.database


def current_v2_head() -> str:
    config = Config(str(_REPOSITORY_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = tuple(script.get_heads())
    if len(heads) != 1:
        raise RuntimeError("V2 schema must have exactly one Alembic head")
    return heads[0]


@dataclass(frozen=True)
class BackfillConfig:
    source_url: str
    target_url: str
    dump_path: Path
    source_revision: str
    output_dir: Path
    batch_size: int = 500
    workers: int = 1
    shuffle_seed: int = 0
    frozen_manifest_path: Path | None = None

    def __post_init__(self) -> None:
        _postgres_url(self.source_url, label="source URL")
        _postgres_url(self.target_url, label="target URL")
        if database_identity(self.source_url) == database_identity(self.target_url):
            raise ValueError("source and target must be different PostgreSQL databases")

        dump_path = Path(self.dump_path)
        output_dir = Path(self.output_dir)
        object.__setattr__(self, "dump_path", dump_path)
        object.__setattr__(self, "output_dir", output_dir)
        if self.frozen_manifest_path is not None:
            frozen_manifest_path = Path(self.frozen_manifest_path)
            object.__setattr__(self, "frozen_manifest_path", frozen_manifest_path)
            if not frozen_manifest_path.is_file():
                raise ValueError(
                    "frozen manifest path must be an existing regular file"
                )
        if not dump_path.is_file():
            raise ValueError("dump path must be an existing regular file")
        if not self.source_revision.strip():
            raise ValueError("source revision must be nonblank")
        if self.batch_size < 1:
            raise ValueError("batch size must be positive")
        if self.workers < 1:
            raise ValueError("worker count must be positive")
        if self.shuffle_seed < 0:
            raise ValueError("shuffle seed must be nonnegative")


__all__ = [
    "BackfillConfig",
    "current_v2_head",
    "database_identity",
]
