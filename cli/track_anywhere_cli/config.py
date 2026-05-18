from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import make_url


DEFAULT_DATABASE_URL = "sqlite:///./.local/track-anywhere.sqlite3"


@dataclass
class CliConfig:
    base_url: str
    token: str | None
    insecure_automation: bool = False


class TokenStore:
    def __init__(self) -> None:
        self.token_file = Path(
            os.getenv(
                "TRACK_ANYWHERE_TOKEN_FILE",
                str(Path.home() / ".config" / "track-anywhere" / "token"),
            )
        )

    def load(self) -> str | None:
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        if keyring is not None:
            token = keyring.get_password("track-anywhere", "cli-token")
            if token:
                return token
        if self.token_file.exists():
            return self.token_file.read_text(encoding="utf-8").strip() or None
        return None

    def save(self, token: str) -> None:
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        if keyring is not None:
            keyring.set_password("track-anywhere", "cli-token", token)
            return
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(token + "\n", encoding="utf-8")
        self.token_file.chmod(0o600)
        print(f"warning: OS keyring unavailable; saved token to {self.token_file}", file=sys.stderr)


def generated_idempotency_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def command_idempotency_key(args: argparse.Namespace, prefix: str) -> str:
    return getattr(args, "idempotency_key", None) or generated_idempotency_key(prefix)


def database_url_from_env() -> str:
    return os.getenv("TRACK_ANYWHERE_DATABASE_URL", DEFAULT_DATABASE_URL)


def sqlite_path_from_database_url(database_url: str) -> Path:
    url = make_url(database_url)
    if url.drivername.split("+", 1)[0] != "sqlite":
        raise RuntimeError("data backup currently supports sqlite databases only")
    if not url.database or url.database == ":memory:":
        raise RuntimeError("data backup requires a file-backed sqlite database")
    return Path(url.database).expanduser()


def safe_backup_label(label: str | None) -> str:
    if not label:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")


def create_sqlite_backup(database_url: str | None = None, output_dir: str | None = None, label: str | None = None) -> dict[str, Any]:
    resolved_database_url = database_url or database_url_from_env()
    source_path = sqlite_path_from_database_url(resolved_database_url)
    if not source_path.exists():
        raise RuntimeError(f"sqlite database not found: {source_path}")

    backup_dir = Path(output_dir).expanduser() if output_dir else source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().astimezone().replace(microsecond=0)
    suffix = source_path.suffix or ".sqlite3"
    label_part = safe_backup_label(label)
    filename_parts = [source_path.stem, created_at.strftime("%Y%m%d-%H%M%S")]
    if label_part:
        filename_parts.append(label_part)
    backup_path = backup_dir / ("-".join(filename_parts) + suffix)

    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)

    return {
        "backup_path": str(backup_path),
        "bytes": backup_path.stat().st_size,
        "created_at": created_at.isoformat(),
        "database_url": resolved_database_url,
        "source_path": str(source_path),
    }


def resolve_token(args: argparse.Namespace) -> str | None:
    if args.token:
        return args.token
    env_token = os.getenv("TRACK_ANYWHERE_TOKEN")
    if env_token:
        if not args.insecure_automation:
            raise RuntimeError("TRACK_ANYWHERE_TOKEN requires --insecure-automation; prefer OS keyring")
        print("warning: using insecure env-token automation", file=sys.stderr)
        return env_token
    return TokenStore().load()
