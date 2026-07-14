from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .command_catalog import CATALOG_COMMAND_HANDLERS, infer_catalog_command_path
from .command_investment import (
    INVESTMENT_COMMAND_HANDLERS,
    infer_investment_command_path,
)
from .command_ledger import LEDGER_COMMAND_HANDLERS, infer_ledger_command_path
from .command_payment import PAYMENT_COMMAND_HANDLERS
from .command_recurring import RECURRING_COMMAND_HANDLERS
from .command_system import SYSTEM_COMMAND_HANDLERS, infer_system_command_path
from .config import CliConfig
from .output import CommandResult
from .runtime import CliCommandSpec, Requester, RuntimeContext


DispatcherResult = tuple[int, Any]
ApiCommandHandler = Callable[[Namespace, CliConfig, Requester], DispatcherResult]

PUBLIC_COMMAND_PATHS = (
    "account.close",
    "account.create",
    "asset.create",
    "auth.login",
    "auth.status",
    "book.balances",
    "book.create",
    "book.reporting_lines",
    "capabilities",
    "category.create",
    "investment.acquire",
    "investment.dispose",
    "release.bump",
    "schema",
    "system.health",
    "system.ready",
    "tx.classify",
    "tx.clear_classification",
    "tx.correct",
    "tx.correct_reference",
    "tx.fx",
    "tx.list",
    "tx.record",
    "tx.reverse",
    "version",
)

LOCAL_COMMAND_PATHS = frozenset(
    {
        "auth.login",
        "auth.status",
        "capabilities",
        "release.bump",
        "schema",
        "version",
    }
)
UNAUTHENTICATED_COMMAND_PATHS = frozenset({"system.health", "system.ready"})
API_COMMAND_PATHS = tuple(
    command_path
    for command_path in PUBLIC_COMMAND_PATHS
    if command_path not in LOCAL_COMMAND_PATHS
)
MUTATING_COMMAND_PATHS = frozenset(
    {
        "account.close",
        "account.create",
        "asset.create",
        "book.create",
        "category.create",
        "investment.acquire",
        "investment.dispose",
        "release.bump",
        "tx.classify",
        "tx.clear_classification",
        "tx.correct",
        "tx.correct_reference",
        "tx.fx",
        "tx.record",
        "tx.reverse",
    }
)
IDEMPOTENCY_KEY_COMMAND_PATHS = frozenset(
    {
        "investment.acquire",
        "investment.dispose",
        "tx.classify",
        "tx.clear_classification",
        "tx.correct",
        "tx.correct_reference",
        "tx.fx",
        "tx.record",
        "tx.reverse",
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
    return CommandResult(
        status=404,
        data={"detail": f"No command handler found for '{command_path}'."},
    )


def _execute_api_command(
    command_path: str,
    args: Namespace,
    context: RuntimeContext,
) -> CommandResult:
    result = dispatch_api_command(
        args,
        context.config,
        context.requester,
        command_path=command_path,
    )
    if result is None:
        return _command_not_found(command_path)
    status, data = result
    return CommandResult(status=status, data=data)


def _api_command_spec(command_path: str) -> CliCommandSpec[Namespace]:
    return CliCommandSpec(
        command_path=command_path,
        requires_auth=command_path not in UNAUTHENTICATED_COMMAND_PATHS,
        execute=lambda args, context, _path=command_path: _execute_api_command(
            _path, args, context
        ),
    )


PUBLIC_COMMAND_SPECS = {
    command_path: _api_command_spec(command_path) for command_path in API_COMMAND_PATHS
}


def infer_command_path(args: Namespace) -> str | None:
    explicit_command_path = getattr(args, "command_path", None)
    if explicit_command_path:
        return explicit_command_path
    for inferer in (
        infer_system_command_path,
        infer_catalog_command_path,
        infer_investment_command_path,
        infer_ledger_command_path,
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
