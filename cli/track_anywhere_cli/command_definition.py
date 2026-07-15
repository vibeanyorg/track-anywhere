from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .config import CliConfig


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    command_path: str
    local: bool
    requires_auth: bool
    mutating: bool
    idempotent: bool
    handler: CommandHandler | None = None

    def __post_init__(self) -> None:
        parts = self.command_path.split(".")
        if any(not part or not part.replace("_", "").isalnum() for part in parts):
            raise ValueError("command_path must contain non-empty slug segments")
        if self.local == (self.handler is not None):
            raise ValueError("exactly API commands must define a request handler")
        if self.idempotent and not self.mutating:
            raise ValueError("only mutating commands can require idempotency keys")

    def __call__(
        self,
        args: Namespace,
        config: CliConfig,
        requester: Requester,
    ) -> tuple[int, Any]:
        if self.handler is None:
            raise TypeError("local commands do not have API request handlers")
        return self.handler(args, config, requester)


def api_command(
    command_path: str,
    *,
    requires_auth: bool = True,
    mutating: bool = False,
    idempotent: bool = False,
) -> Callable[[CommandHandler], CommandDefinition]:
    def decorate(handler: CommandHandler) -> CommandDefinition:
        return CommandDefinition(
            command_path=command_path,
            local=False,
            requires_auth=requires_auth,
            mutating=mutating,
            idempotent=idempotent,
            handler=handler,
        )

    return decorate


def local_command(command_path: str, *, mutating: bool = False) -> CommandDefinition:
    return CommandDefinition(
        command_path=command_path,
        local=True,
        requires_auth=False,
        mutating=mutating,
        idempotent=False,
    )


def index_definitions(
    definitions: Iterable[CommandDefinition],
) -> dict[str, CommandDefinition]:
    indexed: dict[str, CommandDefinition] = {}
    for definition in definitions:
        if definition.command_path in indexed:
            raise RuntimeError(
                f"Duplicate CLI command definition: {definition.command_path}"
            )
        indexed[definition.command_path] = definition
    return indexed


__all__ = [
    "CommandDefinition",
    "CommandHandler",
    "Requester",
    "api_command",
    "index_definitions",
    "local_command",
]
