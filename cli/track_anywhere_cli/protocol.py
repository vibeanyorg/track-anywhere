from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

import click

from .command_entries import ENTRY_COMMAND_PATHS
from .commands import command_definition, command_paths
from .output import CLI_SCHEMA_VERSION


CLI_PACKAGE_NAME = "track-anywhere"
API_VERSION = "v2"


def cli_version() -> str:
    try:
        return version(CLI_PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.1.0"


def version_payload() -> dict[str, Any]:
    return {
        "cli_version": cli_version(),
        "schema_version": CLI_SCHEMA_VERSION,
        "api_version": API_VERSION,
        "supports": supports_payload(),
    }


def capabilities_payload(root: click.Group) -> dict[str, Any]:
    return {
        **version_payload(),
        "commands": [
            _command_summary(root, command_path) for command_path in command_paths()
        ],
    }


def schema_payload(
    root: click.Group,
    command_path: str | None = None,
) -> dict[str, Any]:
    if command_path:
        return {"command": command_schema(root, command_path)}
    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "commands": [_command_summary(root, path) for path in command_paths()],
    }


def command_schema(root: click.Group, command_path: str) -> dict[str, Any]:
    tokens = command_tokens(command_path)
    command = click_command(root, tokens)
    definition = command_definition(command_path)
    return {
        "command_path": command_path,
        "command": ["ta", *tokens],
        "description": command.help or command.short_help or "",
        "side_effects": side_effects(command_path),
        "idempotent": definition.idempotent,
        "supports_dry_run": _has_option(command, "--dry-run"),
        "supports_input_stdin": False,
        "requires_auth": definition.requires_auth,
        "arguments": [
            _argument_schema(param)
            for param in command.params
            if isinstance(param, click.Argument)
        ],
        "flags": [
            _option_schema(param)
            for param in command.params
            if isinstance(param, click.Option)
        ],
        "output": {
            "format": "CliOutcome",
            "schema_version": CLI_SCHEMA_VERSION,
        },
    }


def command_tokens(command_path: str) -> list[str]:
    return [part.replace("_", "-") for part in command_path.split(".")]


def click_command(root: click.Group, tokens: list[str]) -> click.Command:
    command: click.Command = root
    for token in tokens:
        if not isinstance(command, click.Group) or token not in command.commands:
            raise KeyError(" ".join(tokens))
        command = command.commands[token]
    return command


def supports_payload() -> dict[str, Any]:
    return {
        "json_output": True,
        "format_flag": ["human", "json"],
        "agent_mode": True,
        "no_input": True,
        "structured_errors": True,
        "stderr_errors": True,
        "dry_run_commands": sorted((*ENTRY_COMMAND_PATHS, "release.bump")),
        "idempotency_keys": True,
        "agent_requires_explicit_idempotency_key": True,
        "amount_transport": "exact_string",
        "ndjson_output": False,
        "cursor_pagination": True,
    }


def side_effects(command_path: str) -> list[str]:
    if not command_definition(command_path).mutating:
        return []
    return [f"mutates:{command_path}"]


def _command_summary(root: click.Group, command_path: str) -> dict[str, Any]:
    try:
        schema = command_schema(root, command_path)
    except KeyError:
        return {"command_path": command_path, "registered": False}
    return {
        "command_path": schema["command_path"],
        "command": schema["command"],
        "registered": True,
        "side_effects": schema["side_effects"],
        "supports_dry_run": schema["supports_dry_run"],
        "requires_auth": schema["requires_auth"],
    }


def _argument_schema(argument: click.Argument) -> dict[str, Any]:
    return {
        "name": argument.name,
        "required": argument.required,
        "nargs": argument.nargs,
        "type": argument.type.name,
    }


def _option_schema(option: click.Option) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": option.name,
        "opts": list(option.opts),
        "secondary_opts": list(option.secondary_opts),
        "required": option.required,
        "is_flag": option.is_flag,
        "multiple": option.multiple,
        "type": option.type.name,
        "help": option.help or "",
    }
    if isinstance(option.type, click.Choice):
        payload["choices"] = list(option.type.choices)
    default = _jsonable_default(option.default)
    if default is not None:
        payload["default"] = default
    return payload


def _has_option(command: click.Command, option_name: str) -> bool:
    return any(
        isinstance(param, click.Option) and option_name in param.opts
        for param in command.params
    )


def _jsonable_default(value: Any) -> Any | None:
    if value in (None, ()):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return None
