from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


def handle_system_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    if args.command == "system" and args.system_command == "status":
        path = with_query(
            "/api/v1/system/status",
            {"include_counts": "true" if args.include_counts else None},
        )
        return requester(config, "GET", path)
    return None
