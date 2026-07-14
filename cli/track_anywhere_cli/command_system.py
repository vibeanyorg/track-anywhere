from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_system_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any] | None:
    command_path = infer_system_command_path(args)
    if command_path is None:
        return None
    handler = SYSTEM_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_system_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "system":
        return None
    subcommand = getattr(args, "system_command", None)
    if subcommand in {"health", "ready"}:
        return f"system.{subcommand}"
    return None


def request_health(
    _args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v2/health", None, None)


def request_readiness(
    _args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v2/ready", None, None)


SYSTEM_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "system.health": request_health,
    "system.ready": request_readiness,
}
