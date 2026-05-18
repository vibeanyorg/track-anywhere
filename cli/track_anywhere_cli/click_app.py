from __future__ import annotations

from typing import Any

import click

from .click_catalog import register as register_catalog
from .click_common import ClickState, Requester, emit_local, output_options, pass_state
from .click_investment import register as register_investment
from .click_ledger import register as register_ledger
from .click_recurring import register as register_recurring
from .config import CliConfig, TokenStore, create_sqlite_backup, resolve_token
from .exit_codes import EXIT_AUTH, EXIT_SUCCESS, EXIT_VALIDATION
from .http import exit_for_status, request_json


@click.group()
@click.option("--base-url", envvar="TRACK_ANYWHERE_API", default="http://localhost:8000")
@click.option("--token", default=None, help="Bearer token. Prefer OS keyring; this is for one-shot use.")
@click.option("--insecure-automation", is_flag=True, help="Allow env-token automation with warning.")
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON by default.")
@click.option("--no-color", is_flag=True, help="Disable colored human output.")
@click.pass_context
def cli(ctx, base_url: str, token: str | None, insecure_automation: bool, json_mode: bool, no_color: bool):
    """Track Anywhere command line interface."""
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    requester = obj.get("requester", request_json)
    ctx.obj = ClickState(
        base_url=base_url,
        token=token,
        insecure_automation=insecure_automation,
        json_mode=json_mode,
        no_color=no_color,
        requester=requester,
    )


@cli.command("login")
@click.argument("token")
def login(token: str) -> int:
    TokenStore().save(token)
    click.echo("token saved")
    return EXIT_SUCCESS


@cli.group()
def auth():
    """Authentication commands."""


@auth.command("login")
@click.argument("token")
def auth_login(token: str) -> int:
    TokenStore().save(token)
    click.echo("token saved")
    return EXIT_SUCCESS


@auth.command("dev-token")
@output_options
@pass_state
def auth_dev_token(state: ClickState, json_mode: bool, no_color: bool) -> int:
    config = CliConfig(base_url=state.base_url, token=None, insecure_automation=state.insecure_automation)
    status, data = state.requester(config, "POST", "/api/v1/auth/dev-token")
    if status < 400:
        TokenStore().save(data["token"])
    if state.json_mode or json_mode:
        return emit_status(data, status, state=state, json_mode=json_mode, no_color=no_color, command_path="auth.dev_token")
    return emit_status("token saved" if status < 400 else data, status, state=state, json_mode=False, no_color=no_color)


@auth.command("status")
@output_options
@pass_state
def auth_status(state: ClickState, json_mode: bool, no_color: bool) -> int:
    args = _state_args(state)
    try:
        token = resolve_token(args)
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        return EXIT_AUTH
    data = {
        "authenticated": token is not None,
        "base_url": state.base_url,
        "token_source": "configured" if token else None,
    }
    if state.json_mode or json_mode:
        return emit_local(data, state=state, json_mode=json_mode, no_color=no_color, command_path="auth.status")
    return emit_local("authenticated" if token else "not authenticated", state=state, json_mode=False, no_color=no_color)


@cli.group()
def data():
    """Local data commands."""


@data.command("backup")
@click.option("--database-url")
@click.option("--output-dir")
@click.option("--label")
@output_options
@pass_state
def data_backup(state: ClickState, json_mode: bool, no_color: bool, database_url: str | None, output_dir: str | None, label: str | None) -> int:
    try:
        backup = create_sqlite_backup(database_url, output_dir, label)
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        return EXIT_VALIDATION
    payload = {"backup": backup}
    if state.json_mode or json_mode:
        return emit_local(payload, state=state, json_mode=json_mode, no_color=no_color, command_path="data.backup")
    return emit_local(f"backup created: {backup['backup_path']}", state=state, json_mode=False, no_color=no_color)


def emit_status(data: Any, status: int, *, state: ClickState, json_mode: bool, no_color: bool, command_path: str = "") -> int:
    emit_local(data, state=state, json_mode=json_mode, no_color=no_color, command_path=command_path)
    return exit_for_status(status, data)


def _state_args(state: ClickState):
    from argparse import Namespace

    return Namespace(token=state.token, insecure_automation=state.insecure_automation)


def run(argv: list[str] | None = None, *, requester: Requester = request_json) -> int:
    try:
        result = cli.main(args=argv, prog_name="ta", obj={"requester": requester}, standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        return EXIT_VALIDATION
    return int(result or EXIT_SUCCESS)


register_catalog(cli)
register_investment(cli)
register_ledger(cli)
register_recurring(cli)
