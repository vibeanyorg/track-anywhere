from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .command_catalog import CATALOG_COMMAND_HANDLERS, infer_catalog_command_path
from .command_investment import INVESTMENT_COMMAND_HANDLERS, infer_investment_command_path
from .command_ledger import LEDGER_COMMAND_HANDLERS, infer_ledger_command_path
from .command_payment import PAYMENT_COMMAND_HANDLERS, infer_payment_command_path
from .command_recurring import RECURRING_COMMAND_HANDLERS, infer_recurring_command_path
from .command_system import SYSTEM_COMMAND_HANDLERS, infer_system_command_path
from .config import CliConfig
from .output import CommandResult
from .runtime import CliCommandSpec, Requester, RuntimeContext


DispatcherResult = tuple[int, Any]
ApiCommandHandler = Callable[[Namespace, CliConfig, Requester], DispatcherResult]
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
    "counterparty.create",
    "counterparty.ensure",
    "counterparty.list",
    "counterparty.show",
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
    "payment.instrument.create",
    "payment.instrument.list",
    "payment.instrument.show",
    "payment.profile.create",
    "payment.profile.list",
    "payment.profile.status",
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
        "counterparty.create",
        "counterparty.ensure",
        "credit_card.update",
        "draft.confirm",
        "expense.record",
        "income.record",
        "investment.event",
        "payment.instrument.create",
        "payment.profile.create",
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


API_COMMAND_HANDLERS: dict[str, ApiCommandHandler] = {
    **SYSTEM_COMMAND_HANDLERS,
    **CATALOG_COMMAND_HANDLERS,
    **INVESTMENT_COMMAND_HANDLERS,
    **LEDGER_COMMAND_HANDLERS,
    **PAYMENT_COMMAND_HANDLERS,
    **RECURRING_COMMAND_HANDLERS,
}


missing_api_handlers = sorted(set(API_COMMAND_PATHS) - set(API_COMMAND_HANDLERS))
if missing_api_handlers:
    missing_list = ", ".join(missing_api_handlers)
    raise RuntimeError(f"Missing API command handlers: {missing_list}")


def _command_not_found(command_path: str) -> CommandResult:
    return CommandResult(status=404, data={"detail": f"No command handler found for '{command_path}'."})


def _execute_api_command(command_path: str, args: Namespace, context: RuntimeContext) -> CommandResult:
    result = dispatch_api_command(args, context.config, context.requester, command_path=command_path)
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


def infer_command_path(args: Namespace) -> str | None:
    explicit_command_path = getattr(args, "command_path", None)
    if explicit_command_path:
        return explicit_command_path
    for inferer in (
        infer_system_command_path,
        infer_catalog_command_path,
        infer_investment_command_path,
        infer_ledger_command_path,
        infer_payment_command_path,
        infer_recurring_command_path,
    ):
        command_path = inferer(args)
        if command_path is not None:
            return command_path
    return None


def dispatch_api_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
    *,
    command_path: str | None = None,
) -> DispatcherResult | None:
    resolved_command_path = command_path or infer_command_path(args)
    if resolved_command_path is None:
        return None
    handler = API_COMMAND_HANDLERS.get(resolved_command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def command_paths() -> list[str]:
    return sorted(PUBLIC_COMMAND_PATHS)


def command_spec(command_path: str) -> CliCommandSpec[Namespace]:
    return PUBLIC_COMMAND_SPECS[command_path]


def command_specs() -> dict[str, CliCommandSpec[Namespace]]:
    return dict(PUBLIC_COMMAND_SPECS)
