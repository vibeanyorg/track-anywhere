from __future__ import annotations

import os

import click

from .click_catalog import register as register_catalog
from .click_common import ClickState, Requester, output_options, pass_state
from .click_investment import register as register_investment
from .click_ledger import register as register_ledger
from .click_recurring import register as register_recurring
from .config import CliConfig, TokenStore, create_sqlite_backup, resolve_token_with_diagnostics
from .exit_codes import EXIT_SUCCESS, EXIT_VALIDATION
from .http import request_json
from .interaction import ClickInteraction, Interaction, inform
from .oauth_login import DEFAULT_CLI_SCOPE, DEFAULT_WEB_URL, create_browser_login_request, exchange_callback_for_token
from .output import CliDiagnostic
from .renderers import emit_outcome
from .runtime import build_outcome


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
@click.argument("token", required=False)
@click.option("--web-url", envvar="TRACK_ANYWHERE_WEB_URL", default=DEFAULT_WEB_URL)
@click.option("--scope", default=DEFAULT_CLI_SCOPE)
@click.option("--client-id", default="track-anywhere-web")
@click.option("--no-browser", is_flag=True)
@click.option("--callback", "callback_value", hidden=True)
@output_options
@pass_state
def login(
    state: ClickState,
    json_mode: bool,
    no_color: bool,
    token: str | None,
    web_url: str,
    scope: str,
    client_id: str,
    no_browser: bool,
    callback_value: str | None,
) -> int:
    return run_login(
        state,
        json_mode=json_mode,
        no_color=no_color,
        token=token,
        web_url=web_url,
        scope=scope,
        client_id=client_id,
        no_browser=no_browser,
        callback_value=callback_value,
    )


@cli.group()
def auth():
    """Authentication commands."""


@auth.command("login")
@click.argument("token", required=False)
@click.option("--web-url", envvar="TRACK_ANYWHERE_WEB_URL", default=DEFAULT_WEB_URL)
@click.option("--scope", default=DEFAULT_CLI_SCOPE)
@click.option("--client-id", default="track-anywhere-web")
@click.option("--no-browser", is_flag=True)
@click.option("--callback", "callback_value", hidden=True)
@output_options
@pass_state
def auth_login(
    state: ClickState,
    json_mode: bool,
    no_color: bool,
    token: str | None,
    web_url: str,
    scope: str,
    client_id: str,
    no_browser: bool,
    callback_value: str | None,
) -> int:
    return run_login(
        state,
        json_mode=json_mode,
        no_color=no_color,
        token=token,
        web_url=web_url,
        scope=scope,
        client_id=client_id,
        no_browser=no_browser,
        callback_value=callback_value,
    )


@auth.command("dev-token")
@output_options
@pass_state
def auth_dev_token(state: ClickState, json_mode: bool, no_color: bool) -> int:
    output_json = state.json_mode or json_mode
    output_no_color = state.no_color or no_color
    config = CliConfig(base_url=state.base_url, token=None, insecure_automation=state.insecure_automation)
    status, data = state.requester(config, "POST", "/api/v1/auth/dev-token")
    diagnostics: list[CliDiagnostic] = []
    if status < 400 and isinstance(data, dict):
        token = data.get("token")
        if isinstance(token, str):
            diagnostics = _save_token(token)
    outcome = build_outcome("auth.dev_token", status, data, diagnostics=diagnostics)
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


@auth.command("status")
@output_options
@pass_state
def auth_status(state: ClickState, json_mode: bool, no_color: bool) -> int:
    args = _state_args(state)
    output_json = state.json_mode or json_mode
    output_no_color = state.no_color or no_color
    try:
        token_resolution = resolve_token_with_diagnostics(args)
    except RuntimeError as exc:
        outcome = build_outcome("auth.status", 401, {"detail": str(exc)})
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    token = token_resolution.token
    data = {
        "authenticated": token is not None,
        "base_url": state.base_url,
        "token_source": _token_source(state, token),
    }
    outcome = build_outcome("auth.status", 200, data, diagnostics=token_resolution.diagnostics)
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


@cli.group()
def data():
    """Local data commands."""


@data.command("backup")
@click.option("--database-url")
@click.option("--output-dir")
@click.option("--label")
@output_options
@pass_state
def data_backup(
    state: ClickState,
    json_mode: bool,
    no_color: bool,
    database_url: str | None,
    output_dir: str | None,
    label: str | None,
) -> int:
    output_json = state.json_mode or json_mode
    output_no_color = state.no_color or no_color
    try:
        backup = create_sqlite_backup(database_url, output_dir, label)
        outcome = build_outcome("data.backup", 200, {"backup": backup})
    except RuntimeError as exc:
        outcome = build_outcome("data.backup", 400, {"detail": str(exc)}, exit_code=EXIT_VALIDATION)
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


def run_login(
    state: ClickState,
    *,
    json_mode: bool,
    no_color: bool,
    token: str | None,
    web_url: str,
    scope: str,
    client_id: str,
    no_browser: bool,
    callback_value: str | None,
    interaction: Interaction | None = None,
) -> int:
    output_json = state.json_mode or json_mode
    output_no_color = state.no_color or no_color
    interaction = interaction or ClickInteraction(open_browser=not no_browser)

    if token:
        diagnostics = _save_token(token)
        outcome = build_outcome("auth.login", 200, {"authenticated": True, "token_saved": True}, diagnostics=diagnostics)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    login_request = create_browser_login_request(web_url=web_url, client_id=client_id, scope=scope)
    interaction.open_url(login_request.auth_url)
    inform(interaction, "Open this URL to authorize Track Anywhere CLI:")
    inform(interaction, login_request.auth_url)
    callback = callback_value or interaction.prompt("Paste the callback URL")
    try:
        status, data = exchange_callback_for_token(
            request=login_request,
            callback_value=callback,
            config=CliConfig(
                base_url=state.base_url,
                token=None,
                insecure_automation=state.insecure_automation,
            ),
            requester=state.requester,
        )
    except ValueError as exc:
        outcome = build_outcome("auth.login", 400, {"detail": str(exc)}, exit_code=EXIT_VALIDATION)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    except Exception as exc:
        outcome = build_outcome("auth.login", 500, {"detail": f"token exchange failed: {exc}"})
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    if status >= 400:
        outcome = build_outcome("auth.login", status, data)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        outcome = build_outcome(
            "auth.login",
            400,
            {"detail": "token endpoint did not return an access token"},
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    diagnostics = _save_token(access_token)
    scope_value = data.get("scope") if isinstance(data, dict) else None
    outcome = build_outcome(
        "auth.login",
        status,
        {"authenticated": True, "token_saved": True, "scope": scope_value},
        diagnostics=diagnostics,
    )
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


def _state_args(state: ClickState):
    from argparse import Namespace

    return Namespace(token=state.token, insecure_automation=state.insecure_automation)


def _save_token(token: str) -> list[CliDiagnostic]:
    diagnostics = TokenStore().save(token)
    return diagnostics if isinstance(diagnostics, list) else []


def _token_source(state: ClickState, token: str | None) -> str | None:
    if token is None:
        return None
    if state.token:
        return "configured"
    if os.getenv("TRACK_ANYWHERE_TOKEN"):
        return "environment"
    return "keyring"


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
