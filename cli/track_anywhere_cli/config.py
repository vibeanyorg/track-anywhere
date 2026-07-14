from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .output import CliDiagnostic


@dataclass
class CliConfig:
    base_url: str
    token: str | None
    insecure_automation: bool = False


@dataclass(frozen=True)
class StoredToken:
    token: str
    source: str


class TokenStore:
    def __init__(self) -> None:
        self.explicit_token_file = "TRACK_ANYWHERE_TOKEN_FILE" in os.environ
        self.token_file = Path(
            os.getenv(
                "TRACK_ANYWHERE_TOKEN_FILE",
                str(Path.home() / ".config" / "track-anywhere" / "token"),
            )
        )

    def load(self) -> str | None:
        stored = self.load_with_source()
        return stored.token if stored is not None else None

    def load_with_source(self) -> StoredToken | None:
        if self.explicit_token_file:
            return self._load_file()
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        if keyring is not None:
            token = keyring.get_password("track-anywhere", "cli-token")
            if token:
                return StoredToken(token=token, source="keyring")
        return self._load_file()

    def save(self, token: str) -> list[CliDiagnostic]:
        if self.explicit_token_file:
            self._save_file(token)
            return []
        try:
            import keyring  # type: ignore
        except Exception:
            keyring = None
        if keyring is not None:
            try:
                keyring.set_password("track-anywhere", "cli-token", token)
                return []
            except Exception:
                pass
        self._save_file(token)
        return [
            CliDiagnostic(
                level="warning",
                code="token_file_fallback",
                message=f"OS keyring unavailable; saved token to {self.token_file}.",
            )
        ]

    def _load_file(self) -> StoredToken | None:
        if self.token_file.exists():
            token = self.token_file.read_text(encoding="utf-8").strip()
            if token:
                return StoredToken(token=token, source="token_file")
        return None

    def _save_file(self, token: str) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(token + "\n", encoding="utf-8")
        self.token_file.chmod(0o600)


def generated_idempotency_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def command_idempotency_key(args: argparse.Namespace, prefix: str) -> str:
    return getattr(args, "idempotency_key", None) or generated_idempotency_key(prefix)


@dataclass(frozen=True)
class TokenResolution:
    token: str | None
    diagnostics: list[CliDiagnostic]
    source: str | None = None


def resolve_token_with_diagnostics(args: argparse.Namespace) -> TokenResolution:
    if args.token:
        return TokenResolution(token=args.token, diagnostics=[], source="configured")

    env_token = os.getenv("TRACK_ANYWHERE_TOKEN")
    if env_token:
        if not args.insecure_automation:
            raise RuntimeError(
                "TRACK_ANYWHERE_TOKEN requires --insecure-automation; prefer OS keyring"
            )
        return TokenResolution(
            token=env_token,
            diagnostics=[
                CliDiagnostic(
                    level="warning",
                    code="insecure_env_token",
                    message="Using TRACK_ANYWHERE_TOKEN with --insecure-automation.",
                )
            ],
            source="environment",
        )

    stored = TokenStore().load_with_source()
    if stored is None:
        return TokenResolution(token=None, diagnostics=[], source=None)
    return TokenResolution(token=stored.token, diagnostics=[], source=stored.source)


def resolve_token(args: argparse.Namespace) -> str | None:
    return resolve_token_with_diagnostics(args).token
