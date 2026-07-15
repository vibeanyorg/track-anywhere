from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass
from typing import Any, Callable

import click

from .commands import command_definition, dispatch_api_command
from .config import CliConfig, resolve_token_with_diagnostics
from .exit_codes import EXIT_VALIDATION
from .output import CliDiagnostic
from .renderers import emit_outcome, emit_result
from .runtime import build_outcome


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]
]


@dataclass
class ClickState:
    base_url: str
    token: str | None
    insecure_automation: bool
    json_mode: bool
    no_color: bool
    requester: Requester
    resource: str | None = None
    api_key_file: Any | None = None
    no_input: bool = False
    agent_mode: bool = False


pass_state = click.make_pass_decorator(ClickState)


def output_options(fn):
    fn = click.option(
        "--agent",
        is_flag=True,
        expose_value=False,
        callback=_agent_option,
        help="Agent mode: JSON, no color, no input.",
    )(fn)
    fn = click.option(
        "--no-input",
        is_flag=True,
        expose_value=False,
        callback=_no_input_option,
        help="Fail instead of prompting for input.",
    )(fn)
    fn = click.option(
        "--format",
        "output_format",
        type=click.Choice(["human", "json"]),
        expose_value=False,
        callback=_format_option,
        help="Output renderer.",
    )(fn)
    fn = click.option("--no-color", is_flag=True, help="Disable colored human output.")(
        fn
    )
    fn = click.option(
        "--json", "json_mode", is_flag=True, help="Emit machine-readable JSON."
    )(fn)
    return fn


def _agent_option(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if value and isinstance(ctx.obj, ClickState):
        ctx.obj.agent_mode = True
        ctx.obj.json_mode = True
        ctx.obj.no_color = True
        ctx.obj.no_input = True


def _no_input_option(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if value and isinstance(ctx.obj, ClickState):
        ctx.obj.no_input = True


def _format_option(
    ctx: click.Context, _param: click.Parameter, value: str | None
) -> None:
    if value == "json" and isinstance(ctx.obj, ClickState):
        ctx.obj.json_mode = True


def common_args(
    state: ClickState, json_mode: bool = False, no_color: bool = False, **values
) -> Namespace:
    return Namespace(
        base_url=state.base_url,
        resource=state.resource,
        token=state.token,
        api_key_file=state.api_key_file,
        insecure_automation=state.insecure_automation,
        json=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
        no_input=state.no_input,
        agent_mode=state.agent_mode,
        **values,
    )


def run_api(args: Namespace, *, state: ClickState, command_path: str) -> int:
    definition = command_definition(command_path)
    if not definition.requires_auth:
        from .config import TokenResolution

        token_resolution = TokenResolution(token=None, diagnostics=[])
    else:
        try:
            token_resolution = resolve_token_with_diagnostics(args)
        except RuntimeError as exc:
            outcome = build_outcome(command_path, 401, {"detail": str(exc)})
            emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
            return outcome.exit_code
        try:
            from .oauth_login import refresh_token_resolution

            token_resolution = refresh_token_resolution(
                token_resolution,
                requester=state.requester,
            )
        except RuntimeError as exc:
            outcome = build_outcome(command_path, 401, {"detail": str(exc)})
            emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
            return outcome.exit_code
    config = CliConfig(
        base_url=args.base_url,
        token=(
            token_resolution.credential.secret
            if token_resolution.credential is not None
            and token_resolution.credential.kind == "oauth"
            else None
        ),
        api_key=(
            token_resolution.credential.secret
            if token_resolution.credential is not None
            and token_resolution.credential.kind == "api_key"
            else None
        ),
        resource=getattr(args, "resource", None),
        insecure_automation=args.insecure_automation,
    )
    if (
        getattr(args, "agent_mode", False)
        and definition.idempotent
        and not getattr(args, "idempotency_key", None)
    ):
        outcome = build_outcome(
            command_path,
            400,
            {
                "detail": "Agent mode requires --idempotency-key for mutating commands.",
                "error": {
                    "code": "idempotency_key_required",
                    "category": "usage",
                    "message": "Agent mode requires --idempotency-key for mutating commands.",
                    "retryable": False,
                    "remediation": [
                        {
                            "description": "Re-run with a stable idempotency key and reuse it on retry.",
                            "command": [
                                "ta",
                                *command_path.replace("_", "-").split("."),
                                "--idempotency-key",
                                "<stable-key>",
                                "--agent",
                            ],
                        }
                    ],
                },
            },
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
        return outcome.exit_code
    try:
        result = dispatch_api_command(
            args, config, state.requester, command_path=command_path
        )
    except Exception as exc:
        outcome = build_outcome(
            command_path,
            503,
            {
                "detail": f"API command failed: {exc}",
                "error": {
                    "code": "api_request_failed",
                    "category": "external_dependency",
                    "message": f"API command failed: {exc}",
                    "retryable": True,
                },
            },
            diagnostics=[
                CliDiagnostic(
                    level="error",
                    code="api_request_failed",
                    category="external_dependency",
                    message=f"API command failed: {exc}",
                    retryable=True,
                    detail={"exception_type": type(exc).__name__},
                )
            ],
        )
        emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
        return outcome.exit_code
    if result is None:
        outcome = build_outcome(
            command_path,
            400,
            {"detail": "unknown command"},
            diagnostics=token_resolution.diagnostics,
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
        return outcome.exit_code
    status, data = result
    outcome = build_outcome(
        command_path, status, data, diagnostics=token_resolution.diagnostics
    )
    emit_outcome(outcome, json_mode=args.json, no_color=args.no_color)
    return outcome.exit_code


def emit_local(
    data: Any,
    *,
    state: ClickState,
    json_mode: bool,
    no_color: bool,
    command_path: str = "",
) -> int:
    emit_result(
        data,
        json_mode=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
        command_path=command_path,
    )
    return 0
