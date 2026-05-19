from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

import click

from .commands import dispatch_api_command
from .config import CliConfig, resolve_token
from .exit_codes import EXIT_AUTH, EXIT_VALIDATION
from .http import exit_for_status
from .renderers import emit_result


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


@dataclass
class ClickState:
    base_url: str
    token: str | None
    insecure_automation: bool
    json_mode: bool
    no_color: bool
    requester: Requester


pass_state = click.make_pass_decorator(ClickState)


def output_options(fn):
    fn = click.option("--no-color", is_flag=True, help="Disable colored human output.")(fn)
    fn = click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON.")(fn)
    return fn


def common_args(state: ClickState, json_mode: bool = False, no_color: bool = False, **values) -> Namespace:
    return Namespace(
        base_url=state.base_url,
        token=state.token,
        insecure_automation=state.insecure_automation,
        json=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
        **values,
    )


def api_command(command_path: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            state = kwargs.pop("state")
            json_mode = kwargs.pop("json_mode", False)
            no_color = kwargs.pop("no_color", False)
            namespace = fn(*args, state=state, json_mode=json_mode, no_color=no_color, **kwargs)
            return run_api(namespace, state=state, command_path=command_path)

        return wrapper

    return decorator


def run_api(args: Namespace, *, state: ClickState, command_path: str) -> int:
    try:
        token = resolve_token(args)
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        return EXIT_AUTH
    config = CliConfig(base_url=args.base_url, token=token, insecure_automation=args.insecure_automation)
    result = dispatch_api_command(args, config, state.requester)
    if result is None:
        click.echo("unknown command", err=True)
        return EXIT_VALIDATION
    status, data = result
    emit_result(
        data,
        json_mode=args.json,
        no_color=args.no_color,
        command_path=command_path,
        status=status,
    )
    return exit_for_status(status, data)


def emit_local(data: Any, *, state: ClickState, json_mode: bool, no_color: bool, command_path: str = "") -> int:
    emit_result(
        data,
        json_mode=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
        command_path=command_path,
    )
    return 0
