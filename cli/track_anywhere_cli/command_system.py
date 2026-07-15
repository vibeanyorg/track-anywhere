from __future__ import annotations

from argparse import Namespace
from typing import Any

from .command_definition import CommandDefinition, Requester, api_command
from .config import CliConfig


@api_command("system.health", requires_auth=False)
def request_health(
    _args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v2/health", None, None)


@api_command("system.ready", requires_auth=False)
def request_readiness(
    _args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return requester(config, "GET", "/api/v2/ready", None, None)


SYSTEM_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    request_health,
    request_readiness,
)
