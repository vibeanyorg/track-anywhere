from __future__ import annotations

from .click_app import cli, run
from .config import CliConfig, TokenStore, create_sqlite_backup, resolve_token
from .exit_codes import (
    EXIT_AUTH,
    EXIT_EXTERNAL_DEPENDENCY,
    EXIT_IDEMPOTENCY_CONFLICT,
    EXIT_INTERNAL,
    EXIT_NOT_FOUND,
    EXIT_POLICY_DENIED,
    EXIT_SECURITY_PRECONDITION,
    EXIT_STALE_VERSION,
    EXIT_SUCCESS,
    EXIT_VALIDATION,
)
from .http import exit_for_status, request_json, with_query


def main(argv: list[str] | None = None) -> int:
    return run(argv, requester=request_json)


__all__ = [
    "CliConfig",
    "EXIT_AUTH",
    "EXIT_EXTERNAL_DEPENDENCY",
    "EXIT_IDEMPOTENCY_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_POLICY_DENIED",
    "EXIT_SECURITY_PRECONDITION",
    "EXIT_STALE_VERSION",
    "EXIT_SUCCESS",
    "EXIT_VALIDATION",
    "TokenStore",
    "cli",
    "create_sqlite_backup",
    "exit_for_status",
    "main",
    "request_json",
    "resolve_token",
    "with_query",
]


if __name__ == "__main__":
    raise SystemExit(main())
