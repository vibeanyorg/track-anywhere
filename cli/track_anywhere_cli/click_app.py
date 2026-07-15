from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from .click_auth import register as register_auth
from .click_catalog import register as register_catalog
from .click_common import ClickState, Requester, output_options, pass_state
from .click_credit_card import register as register_credit_card
from .click_investment import register as register_investment
from .click_ledger import register as register_ledger
from .click_system import register as register_system
from .exit_codes import EXIT_SUCCESS, EXIT_VALIDATION
from .http import request_json
from .protocol import capabilities_payload, schema_payload, version_payload
from .release_version import (
    ReleaseVersionError,
    apply_version_bump,
    build_version_bump_plan,
)
from .renderers import emit_outcome
from .runtime import build_outcome


@click.group()
@click.option(
    "--base-url",
    envvar=["TRACK_ANYWHERE_API", "TRACK_ANYWHERE_SERVICE_URL"],
    default="http://localhost:8000",
)
@click.option(
    "--token",
    default=None,
    help="Bearer token. Prefer OS keyring; this is for one-shot use.",
)
@click.option(
    "--insecure-automation",
    is_flag=True,
    help="Allow env-token automation with warning.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json"]),
    default=None,
    help="Output renderer.",
)
@click.option(
    "--json", "json_mode", is_flag=True, help="Emit machine-readable JSON by default."
)
@click.option("--no-color", is_flag=True, help="Disable colored human output.")
@click.option("--no-input", is_flag=True, help="Fail instead of prompting for input.")
@click.option(
    "--agent", "agent_mode", is_flag=True, help="Agent mode: JSON, no color, no input."
)
@click.pass_context
def cli(
    ctx,
    base_url: str,
    token: str | None,
    insecure_automation: bool,
    output_format: str | None,
    json_mode: bool,
    no_color: bool,
    no_input: bool,
    agent_mode: bool,
):
    """Track Anywhere command line interface."""
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    requester = obj.get("requester", request_json)
    env_agent = _env_truthy("TRACK_ANYWHERE_AGENT")
    effective_agent = agent_mode or env_agent
    ctx.obj = ClickState(
        base_url=base_url,
        token=token,
        insecure_automation=insecure_automation,
        json_mode=json_mode or output_format == "json" or effective_agent,
        no_color=no_color or effective_agent,
        no_input=no_input or effective_agent,
        agent_mode=effective_agent,
        requester=requester,
    )


@cli.group()
def release():
    """Release automation commands."""


@release.command("bump")
@click.option(
    "--part",
    type=click.Choice(["major", "minor", "patch"]),
    default="patch",
    show_default=True,
)
@click.option(
    "--to",
    "target_version",
    help="Set an exact semver target instead of incrementing a part.",
)
@click.option(
    "--project-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("pyproject.toml"),
    show_default=True,
)
@click.option(
    "--apply", "apply_changes", is_flag=True, help="Apply the version change."
)
@click.option(
    "--dry-run", is_flag=True, help="Preview the version change without writing files."
)
@click.option("--confirm", help="Required with --apply. Must equal the target version.")
@click.option(
    "--allow-dirty",
    is_flag=True,
    help="Allow applying with an existing dirty git worktree.",
)
@output_options
@pass_state
def release_bump(
    state: ClickState,
    json_mode: bool,
    no_color: bool,
    part: str,
    target_version: str | None,
    project_file: Path,
    apply_changes: bool,
    dry_run: bool,
    confirm: str | None,
    allow_dirty: bool,
) -> int:
    output_json = state.json_mode or json_mode
    output_no_color = state.no_color or no_color
    try:
        if dry_run and apply_changes:
            raise ReleaseVersionError(
                "conflicting_flags",
                "Use either --dry-run or --apply, not both.",
                remediation=[
                    {
                        "description": "Preview the bump.",
                        "command": ["ta", "release", "bump", "--dry-run", "--agent"],
                    }
                ],
            )
        plan = build_version_bump_plan(
            project_file, part=part, target_version=target_version
        )
        if apply_changes:
            if confirm != plan.next_version:
                code = (
                    "confirmation_required"
                    if confirm is None
                    else "confirmation_mismatch"
                )
                raise ReleaseVersionError(
                    code,
                    f"Applying this bump requires --confirm {plan.next_version}.",
                    remediation=[
                        {
                            "description": "Apply the planned bump with explicit confirmation.",
                            "command": [
                                "ta",
                                "release",
                                "bump",
                                "--apply",
                                "--confirm",
                                plan.next_version,
                                "--agent",
                            ],
                        }
                    ],
                )
            apply_version_bump(plan, allow_dirty=allow_dirty)
            payload = plan.to_payload(dry_run=False, applied=True)
        else:
            payload = plan.to_payload(dry_run=True, applied=False)
        outcome = build_outcome("release.bump", 200, payload)
    except ReleaseVersionError as exc:
        outcome = build_outcome(
            "release.bump",
            400,
            {
                "detail": exc.message,
                "error": {
                    "code": exc.code,
                    "category": "usage",
                    "message": exc.message,
                    "retryable": False,
                    "remediation": exc.remediation,
                },
            },
            exit_code=EXIT_VALIDATION,
        )
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


@cli.command("version")
@output_options
@pass_state
def version_command(state: ClickState, json_mode: bool, no_color: bool) -> int:
    outcome = build_outcome("version", 200, version_payload())
    emit_outcome(
        outcome,
        json_mode=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
    )
    return outcome.exit_code


@cli.command("capabilities")
@output_options
@pass_state
def capabilities_command(state: ClickState, json_mode: bool, no_color: bool) -> int:
    outcome = build_outcome("capabilities", 200, capabilities_payload(cli))
    emit_outcome(
        outcome,
        json_mode=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
    )
    return outcome.exit_code


@cli.command("schema")
@click.argument("command_path", required=False)
@output_options
@pass_state
def schema_command(
    state: ClickState, json_mode: bool, no_color: bool, command_path: str | None
) -> int:
    try:
        payload = schema_payload(cli, command_path)
        outcome = build_outcome("schema", 200, payload)
    except KeyError:
        outcome = build_outcome(
            "schema",
            404,
            {
                "detail": f"Unknown command path: {command_path}",
                "error": {
                    "code": "command_schema_not_found",
                    "category": "not_found",
                    "message": f"Unknown command path: {command_path}",
                    "retryable": False,
                    "remediation": [
                        {
                            "description": "List known command schemas.",
                            "command": ["ta", "schema", "--agent"],
                        }
                    ],
                },
            },
        )
    emit_outcome(
        outcome,
        json_mode=state.json_mode or json_mode,
        no_color=state.no_color or no_color,
    )
    return outcome.exit_code


def run(argv: list[str] | None = None, *, requester: Requester = request_json) -> int:
    try:
        result = cli.main(
            args=argv,
            prog_name="ta",
            obj={"requester": requester},
            standalone_mode=False,
        )
    except click.ClickException as exc:
        if _wants_machine_output(argv):
            outcome = build_outcome(
                "cli.parse",
                400,
                {
                    "detail": exc.format_message(),
                    "error": {
                        "code": _click_error_code(exc),
                        "category": "usage",
                        "message": exc.format_message(),
                        "retryable": False,
                        "remediation": [
                            {
                                "description": "Inspect command syntax.",
                                "command": ["ta", "--help", "--agent"],
                            }
                        ],
                    },
                },
                exit_code=exc.exit_code,
            )
            emit_outcome(outcome, json_mode=True, no_color=True)
            return exc.exit_code
        exc.show()
        return exc.exit_code
    except click.Abort:
        if _wants_machine_output(argv):
            outcome = build_outcome(
                "cli.parse",
                400,
                {
                    "detail": "Command aborted.",
                    "error": {
                        "code": "aborted",
                        "category": "usage",
                        "message": "Command aborted.",
                        "retryable": False,
                    },
                },
                exit_code=EXIT_VALIDATION,
            )
            emit_outcome(outcome, json_mode=True, no_color=True)
            return outcome.exit_code
        return EXIT_VALIDATION
    return int(result or EXIT_SUCCESS)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def _wants_machine_output(argv: list[str] | None) -> bool:
    args = argv if argv is not None else sys.argv[1:]
    return (
        _env_truthy("TRACK_ANYWHERE_AGENT")
        or any(arg in {"--json", "--agent"} for arg in args)
        or _format_json_requested(args)
    )


def _format_json_requested(args: list[str]) -> bool:
    return any(arg == "--format=json" for arg in args) or any(
        left == "--format" and right == "json" for left, right in zip(args, args[1:])
    )


def _click_error_code(exc: click.ClickException) -> str:
    if isinstance(exc, click.NoSuchOption):
        return "unknown_option"
    if isinstance(exc, click.MissingParameter):
        return "missing_required_argument"
    if isinstance(exc, click.BadParameter):
        return "invalid_argument"
    if isinstance(exc, click.UsageError):
        message = exc.format_message().lower()
        if "no such command" in message:
            return "unknown_command"
        return "usage_error"
    return "cli_error"


register_auth(cli)
register_catalog(cli)
register_credit_card(cli)
register_investment(cli)
register_ledger(cli)
register_system(cli)
