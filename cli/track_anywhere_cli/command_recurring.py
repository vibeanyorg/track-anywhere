from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .command_payment import unsupported_capability
from .config import CliConfig


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_recurring_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any] | None:
    command_path = infer_recurring_command_path(args)
    if command_path is None:
        return None
    return unsupported_capability(command_path)


def infer_recurring_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "recurring":
        return None
    subcommand = getattr(args, "recurring_command", None)
    if not subcommand:
        return "recurring"
    return f"recurring.{subcommand.replace('-', '_')}"


def _unsupported_handler(
    args: Namespace,
    _config: CliConfig,
    _requester: Requester,
) -> tuple[int, Any]:
    return unsupported_capability(infer_recurring_command_path(args) or "recurring")


RECURRING_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "recurring.create": _unsupported_handler,
    "recurring.list": _unsupported_handler,
    "recurring.show": _unsupported_handler,
    "recurring.update": _unsupported_handler,
    "recurring.reminders": _unsupported_handler,
    "recurring.draft_due": _unsupported_handler,
}
