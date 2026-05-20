from __future__ import annotations

import os
from argparse import Namespace

import click

from .click_common import ClickState, output_options, pass_state
from .config import CliConfig, TokenStore, resolve_token_with_diagnostics
from .exit_codes import EXIT_VALIDATION
from .interaction import ClickInteraction, Interaction, inform
from .oauth_login import DEFAULT_CLI_SCOPE, DEFAULT_WEB_URL, create_browser_login_request, exchange_callback_for_token
from .output import CliDiagnostic
from .renderers import emit_outcome
from .runtime import build_outcome


def register(root: click.Group) -> None:
    _register_top_level_login(root)
    _register_auth_group(root)


def _register_top_level_login(root: click.Group) -> None:
    @root.command("login")
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


def _register_auth_group(root: click.Group) -> None:
    @root.group()
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
        args = Namespace(token=state.token, insecure_automation=state.insecure_automation)
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

    if state.no_input and not callback_value:
        outcome = build_outcome(
            "auth.login",
            400,
            {
                "detail": "auth login requires a token or --callback in non-interactive mode",
                "error": {
                    "code": "missing_required_input",
                    "category": "usage",
                    "message": "auth login requires a token or --callback in non-interactive mode.",
                    "retryable": False,
                    "remediation": [
                        {"description": "Use an existing bearer token.", "command": ["ta", "auth", "login", "<token>", "--agent"]},
                        {"description": "Complete browser login outside agent mode and pass the callback.", "command": ["ta", "auth", "login", "--callback", "<url>", "--agent"]},
                    ],
                },
            },
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    login_request = create_browser_login_request(web_url=web_url, client_id=client_id, scope=scope)
    if not callback_value:
        interaction.open_url(login_request.auth_url)
        inform(interaction, "Open this URL to authorize Track Anywhere CLI:")
        inform(interaction, login_request.auth_url)
    callback = callback_value or interaction.prompt("Paste the callback URL")
    try:
        status, data = exchange_callback_for_token(
            request=login_request,
            callback_value=callback,
            config=CliConfig(base_url=state.base_url, token=None, insecure_automation=state.insecure_automation),
            requester=state.requester,
        )
    except ValueError as exc:
        code = "security_precondition" if "state" in str(exc).lower() else "validation_error"
        outcome = build_outcome(
            "auth.login",
            400,
            {
                "detail": str(exc),
                "error": {
                    "code": code,
                    "category": "security" if code == "security_precondition" else "usage",
                    "message": str(exc),
                    "retryable": False,
                },
            },
            exit_code=EXIT_VALIDATION,
        )
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
        outcome = build_outcome("auth.login", 400, {"detail": "token endpoint did not return an access token"}, exit_code=EXIT_VALIDATION)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    diagnostics = _save_token(access_token)
    scope_value = data.get("scope") if isinstance(data, dict) else None
    outcome = build_outcome("auth.login", status, {"authenticated": True, "token_saved": True, "scope": scope_value}, diagnostics=diagnostics)
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


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
