from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def unsupported_capability(capability: str) -> tuple[int, dict[str, Any]]:
    message = (
        f"{capability} is not implemented by API V2; "
        "the CLI did not contact the server."
    )
    return 410, {
        "detail": message,
        "error": {
            "code": "capability_not_available_in_v2",
            "category": "unsupported",
            "message": message,
            "retryable": False,
        },
    }


def handle_payment_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any] | None:
    command_path = infer_payment_command_path(args)
    if command_path is None:
        return None
    return unsupported_capability(command_path)


def infer_payment_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "payment":
        return None
    payment_command = getattr(args, "payment_command", None)
    nested = None
    if payment_command == "profile":
        nested = getattr(args, "profile_command", None)
    elif payment_command == "instrument":
        nested = getattr(args, "instrument_command", None)
    if payment_command in {"profile", "instrument"} and nested:
        return f"payment.{payment_command}.{nested}"
    return "payment"


def _unsupported_handler(
    args: Namespace,
    _config: CliConfig,
    _requester: Requester,
) -> tuple[int, Any]:
    return unsupported_capability(infer_payment_command_path(args) or "payment")


PAYMENT_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "payment.profile.create": _unsupported_handler,
    "payment.profile.list": _unsupported_handler,
    "payment.profile.status": _unsupported_handler,
    "payment.instrument.create": _unsupported_handler,
    "payment.instrument.list": _unsupported_handler,
    "payment.instrument.show": _unsupported_handler,
}
