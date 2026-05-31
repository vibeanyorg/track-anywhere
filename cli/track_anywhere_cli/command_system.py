from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_system_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_system_command_path(args)
    if command_path is None:
        return None
    handler = SYSTEM_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_system_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) == "system" and getattr(args, "system_command", None) == "status":
        return "system.status"
    return None


def request_system_status(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    status_query = with_query("/api/v1/system/status", {"include_counts": "true" if args.include_counts else None})
    return requester(config, "GET", status_query)


SYSTEM_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "system.status": request_system_status,
}
