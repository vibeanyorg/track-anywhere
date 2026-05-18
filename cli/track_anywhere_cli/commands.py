from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .command_catalog import handle_catalog_command
from .command_investment import handle_investment_command
from .command_ledger import handle_ledger_command
from .command_recurring import handle_recurring_command
from .config import CliConfig


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandResult = tuple[int, Any]


def dispatch_api_command(args: Namespace, config: CliConfig, requester: Requester) -> CommandResult | None:
    for handler in (handle_catalog_command, handle_investment_command, handle_ledger_command, handle_recurring_command):
        result = handler(args, config, requester)
        if result is not None:
            return result
    return None
