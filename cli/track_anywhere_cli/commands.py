from __future__ import annotations

from argparse import Namespace
from typing import Any

from .command_catalog import handle_catalog_command
from .command_investment import handle_investment_command
from .command_ledger import handle_ledger_command
from .command_recurring import handle_recurring_command
from .command_system import handle_system_command
from .config import CliConfig
from .output import CommandResult
from .runtime import CliCommandSpec, Requester, RuntimeContext


DispatcherResult = tuple[int, Any]
PUBLIC_COMMAND_PATHS = (
    "account.adjust",
    "account.balance",
    "account.create",
    "account.find",
    "account.list",
    "account.show",
    "account.update",
    "auth.dev_token",
    "auth.login",
    "auth.status",
    "balance",
    "balance.adjust",
    "capture",
    "category.create",
    "category.ensure",
    "category.find",
    "category.list",
    "category.show",
    "category.update",
    "credit_card.list",
    "credit_card.show",
    "credit_card.update",
    "data.backup",
    "draft.confirm",
    "expense.record",
    "income.record",
    "investment.event",
    "investment.performance",
    "capabilities",
    "recurring.create",
    "recurring.draft_due",
    "recurring.list",
    "recurring.reminders",
    "recurring.show",
    "recurring.update",
    "release.bump",
    "summary.accounts",
    "summary.categories",
    "system.status",
    "tx.list",
    "tx.record",
    "tx.reclassify",
    "tx.reverse",
    "tx.show",
    "tx.snapshot",
    "user.create",
    "user.list",
    "schema",
    "version",
)
LOCAL_COMMAND_PATHS = frozenset({"auth.dev_token", "auth.login", "auth.status", "capabilities", "data.backup", "release.bump", "schema", "version"})
API_COMMAND_PATHS = tuple(command_path for command_path in PUBLIC_COMMAND_PATHS if command_path not in LOCAL_COMMAND_PATHS)
MUTATING_COMMAND_PATHS = frozenset(
    {
        "account.adjust",
        "account.create",
        "account.update",
        "balance.adjust",
        "capture",
        "category.create",
        "category.ensure",
        "category.update",
        "credit_card.update",
        "draft.confirm",
        "expense.record",
        "income.record",
        "investment.event",
        "recurring.create",
        "recurring.draft_due",
        "recurring.update",
        "release.bump",
        "tx.record",
        "tx.reclassify",
        "tx.reverse",
        "user.create",
    }
)


def _command_not_found(command_path: str) -> CommandResult:
    return CommandResult(status=404, data={"detail": f"No command handler found for '{command_path}'."})


def _execute_api_command(command_path: str, args: Namespace, context: RuntimeContext) -> CommandResult:
    result = dispatch_api_command(args, context.config, context.requester)
    if result is None:
        return _command_not_found(command_path)
    status, data = result
    return CommandResult(status=status, data=data)


def _api_command_spec(command_path: str) -> CliCommandSpec[Namespace]:
    return CliCommandSpec(
        command_path=command_path,
        requires_auth=True,
        execute=lambda args, context, _path=command_path: _execute_api_command(_path, args, context),
    )


def _build_public_command_specs() -> dict[str, CliCommandSpec[Namespace]]:
    return {command_path: _api_command_spec(command_path) for command_path in API_COMMAND_PATHS}


PUBLIC_COMMAND_SPECS = _build_public_command_specs()


def dispatch_api_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> DispatcherResult | None:
    for handler in (
        handle_system_command,
        handle_catalog_command,
        handle_investment_command,
        handle_ledger_command,
        handle_recurring_command,
    ):
        result = handler(args, config, requester)
        if result is not None:
            return result
    return None


def command_paths() -> list[str]:
    return sorted(PUBLIC_COMMAND_PATHS)


def command_spec(command_path: str) -> CliCommandSpec[Namespace]:
    return PUBLIC_COMMAND_SPECS[command_path]


def command_specs() -> dict[str, CliCommandSpec[Namespace]]:
    return dict(PUBLIC_COMMAND_SPECS)
