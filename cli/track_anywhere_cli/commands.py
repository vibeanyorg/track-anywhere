from __future__ import annotations

from argparse import Namespace
from typing import Any

from .command_archive import ARCHIVE_COMMAND_DEFINITIONS
from .command_catalog import CATALOG_COMMAND_DEFINITIONS
from .command_credit_card import CREDIT_CARD_COMMAND_DEFINITIONS
from .command_definition import CommandDefinition, index_definitions, local_command
from .command_entries import ENTRY_COMMAND_DEFINITIONS
from .command_investment import INVESTMENT_COMMAND_DEFINITIONS
from .command_ledger import LEDGER_COMMAND_DEFINITIONS
from .command_system import SYSTEM_COMMAND_DEFINITIONS
from .config import CliConfig
from .output import CommandResult
from .runtime import CliCommandSpec, Requester, RuntimeContext


DispatcherResult = tuple[int, Any]

AUTH_LOGIN = local_command("auth.login")
AUTH_STATUS = local_command("auth.status")
CAPABILITIES = local_command("capabilities")
RELEASE_BUMP = local_command("release.bump", mutating=True)
SCHEMA = local_command("schema")
VERSION = local_command("version")

LOCAL_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    AUTH_LOGIN,
    AUTH_STATUS,
    CAPABILITIES,
    RELEASE_BUMP,
    SCHEMA,
    VERSION,
)
API_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    *SYSTEM_COMMAND_DEFINITIONS,
    *ARCHIVE_COMMAND_DEFINITIONS,
    *CATALOG_COMMAND_DEFINITIONS,
    *CREDIT_CARD_COMMAND_DEFINITIONS,
    *INVESTMENT_COMMAND_DEFINITIONS,
    *LEDGER_COMMAND_DEFINITIONS,
    *ENTRY_COMMAND_DEFINITIONS,
)
PUBLIC_COMMAND_DEFINITIONS = (*LOCAL_COMMAND_DEFINITIONS, *API_COMMAND_DEFINITIONS)
_COMMAND_DEFINITIONS = index_definitions(PUBLIC_COMMAND_DEFINITIONS)

PUBLIC_COMMAND_PATHS = tuple(sorted(_COMMAND_DEFINITIONS))
LOCAL_COMMAND_PATHS = frozenset(
    definition.command_path for definition in LOCAL_COMMAND_DEFINITIONS
)
UNAUTHENTICATED_COMMAND_PATHS = frozenset(
    definition.command_path
    for definition in API_COMMAND_DEFINITIONS
    if not definition.requires_auth
)
API_COMMAND_PATHS = tuple(
    sorted(definition.command_path for definition in API_COMMAND_DEFINITIONS)
)
MUTATING_COMMAND_PATHS = frozenset(
    definition.command_path
    for definition in PUBLIC_COMMAND_DEFINITIONS
    if definition.mutating
)
IDEMPOTENCY_KEY_COMMAND_PATHS = frozenset(
    definition.command_path
    for definition in API_COMMAND_DEFINITIONS
    if definition.idempotent
)
API_COMMAND_HANDLERS = index_definitions(API_COMMAND_DEFINITIONS)


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


def _api_command_spec(definition: CommandDefinition) -> CliCommandSpec[Namespace]:
    return CliCommandSpec(
        command_path=definition.command_path,
        requires_auth=definition.requires_auth,
        execute=lambda args,
        context,
        _path=definition.command_path: _execute_api_command(_path, args, context),
    )


PUBLIC_COMMAND_SPECS = {
    definition.command_path: _api_command_spec(definition)
    for definition in API_COMMAND_DEFINITIONS
}


def infer_command_path(args: Namespace) -> str | None:
    explicit_command_path = getattr(args, "command_path", None)
    if explicit_command_path:
        return explicit_command_path
    command = getattr(args, "command", None)
    if type(command) is not str or not command:
        return None
    if command in API_COMMAND_HANDLERS:
        return command
    subcommand = getattr(args, f"{command}_command", None)
    if type(subcommand) is not str or not subcommand:
        return None
    candidate = f"{command}.{subcommand.replace('-', '_')}"
    return candidate if candidate in API_COMMAND_HANDLERS else None


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


def command_definitions() -> dict[str, CommandDefinition]:
    return dict(_COMMAND_DEFINITIONS)


def command_definition(command_path: str) -> CommandDefinition:
    return _COMMAND_DEFINITIONS[command_path]


def command_spec(command_path: str) -> CliCommandSpec[Namespace]:
    return PUBLIC_COMMAND_SPECS[command_path]


def command_specs() -> dict[str, CliCommandSpec[Namespace]]:
    return dict(PUBLIC_COMMAND_SPECS)
