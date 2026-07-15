from __future__ import annotations

from argparse import Namespace

import click

from .browser_login import capture_browser_callback
from .click_common import ClickState, output_options, pass_state
from .config import AuthProfile, CliConfig, TokenStore, resolve_token_with_diagnostics
from .device_login import run_device_login
from .exit_codes import EXIT_VALIDATION
from .interaction import ClickInteraction, Interaction
from .oauth_login import (
    DEFAULT_CLI_SCOPE,
    discover_oauth_metadata,
    exchange_callback_for_token,
    profile_from_token_response,
    refresh_token_resolution,
    revoke_oauth_profile,
)
from .output import CliDiagnostic
from .renderers import emit_outcome
from .runtime import build_outcome


def register(root: click.Group) -> None:
    _register_top_level_login(root)
    _register_auth_group(root)


def _login_options(fn):
    fn = click.option("--scope", default=DEFAULT_CLI_SCOPE, show_default=True)(fn)
    fn = click.option(
        "--client-id",
        default=None,
        help="Use an existing public OAuth client instead of dynamic registration.",
    )(fn)
    fn = click.option(
        "--no-browser",
        is_flag=True,
        help="Print the authorization URL without opening a local browser.",
    )(fn)
    fn = click.option(
        "--device",
        is_flag=True,
        help="Use OAuth device authorization for a headless environment.",
    )(fn)
    return fn


def _register_top_level_login(root: click.Group) -> None:
    @root.command("login")
    @_login_options
    @output_options
    @pass_state
    def login(
        state: ClickState,
        json_mode: bool,
        no_color: bool,
        scope: str,
        client_id: str | None,
        no_browser: bool,
        device: bool,
    ) -> int:
        return run_login(
            state,
            json_mode=json_mode,
            no_color=no_color,
            scope=scope,
            client_id=client_id,
            no_browser=no_browser,
            device=device,
        )


def _register_auth_group(root: click.Group) -> None:
    @root.group()
    def auth():
        """Authentication commands."""

    @auth.command("login")
    @_login_options
    @output_options
    @pass_state
    def auth_login(
        state: ClickState,
        json_mode: bool,
        no_color: bool,
        scope: str,
        client_id: str | None,
        no_browser: bool,
        device: bool,
    ) -> int:
        return run_login(
            state,
            json_mode=json_mode,
            no_color=no_color,
            scope=scope,
            client_id=client_id,
            no_browser=no_browser,
            device=device,
        )

    @auth.command("status")
    @output_options
    @pass_state
    def auth_status(state: ClickState, json_mode: bool, no_color: bool) -> int:
        args = Namespace(
            token=state.token,
            api_key_file=state.api_key_file,
            insecure_automation=state.insecure_automation,
            base_url=state.base_url,
            resource=state.resource,
        )
        output_json = state.json_mode or json_mode
        output_no_color = state.no_color or no_color
        try:
            resolution = resolve_token_with_diagnostics(args)
            resolution = refresh_token_resolution(
                resolution,
                requester=state.requester,
            )
        except RuntimeError as exc:
            outcome = build_outcome("auth.status", 401, {"detail": str(exc)})
            emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
            return outcome.exit_code
        credential = resolution.credential
        data = {
            "authenticated": credential is not None,
            "base_url": state.base_url,
            "resource": (
                resolution.profile.resource
                if resolution.profile is not None
                else state.resource
            ),
            "token_source": resolution.source,
            "auth_kind": credential.kind if credential is not None else None,
        }
        if credential is not None:
            status, status_data = state.requester(
                CliConfig(
                    base_url=state.base_url,
                    token=(credential.secret if credential.kind == "oauth" else None),
                    api_key=(
                        credential.secret if credential.kind == "api_key" else None
                    ),
                    resource=data["resource"],
                    insecure_automation=state.insecure_automation,
                ),
                "GET",
                "/api/v2/auth/token-status",
                None,
                None,
            )
            if status < 400 and isinstance(status_data, dict):
                data.update(status_data)
            else:
                detail = (
                    status_data.get("detail")
                    if isinstance(status_data, dict)
                    else status_data
                )
                data.update(
                    {
                        "authenticated": False,
                        "detail": detail or "token validation failed",
                    }
                )
                outcome = build_outcome(
                    "auth.status",
                    status,
                    data,
                    diagnostics=resolution.diagnostics,
                )
                emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
                return outcome.exit_code
        outcome = build_outcome(
            "auth.status", 200, data, diagnostics=resolution.diagnostics
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    @auth.command("logout")
    @output_options
    @pass_state
    def auth_logout(state: ClickState, json_mode: bool, no_color: bool) -> int:
        output_json = state.json_mode or json_mode
        output_no_color = state.no_color or no_color
        store = TokenStore(base_url=state.base_url, resource=state.resource)
        with store.profile_lock():
            try:
                stored = store.load_profile_with_source()
            except (OSError, RuntimeError) as exc:
                outcome = build_outcome(
                    "auth.logout",
                    500,
                    {
                        "authenticated": False,
                        "local_profile_deleted": False,
                        "remote_revoked": False,
                        "resource": store.resource,
                        "detail": str(exc),
                    },
                    diagnostics=[
                        CliDiagnostic(
                            level="error",
                            code="profile_storage_error",
                            category="security",
                            message=str(exc),
                            retryable=False,
                        )
                    ],
                )
                emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
                return outcome.exit_code
            if stored is None:
                outcome = build_outcome(
                    "auth.logout",
                    200,
                    {
                        "authenticated": False,
                        "local_profile_deleted": False,
                        "remote_revoked": False,
                        "resource": store.resource,
                    },
                )
                emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
                return outcome.exit_code

            profile = stored.profile
            remote_revoked, remote_status, remote_data = revoke_oauth_profile(
                profile,
                requester=state.requester,
            )
            deletion = store._delete_profile_locked()
        local_deleted = deletion.deleted
        data = {
            "authenticated": not remote_revoked and not local_deleted,
            "local_profile_deleted": local_deleted,
            "remote_revoked": remote_revoked,
            "resource": profile.resource,
        }
        if not local_deleted:
            data["detail"] = "OAuth profile could not be removed from local storage"
        elif not remote_revoked:
            data["detail"] = (
                remote_data.get("detail")
                if isinstance(remote_data, dict)
                else str(remote_data)
            )
        status = (
            remote_status if not remote_revoked else (500 if not local_deleted else 200)
        )
        outcome = build_outcome(
            "auth.logout",
            status,
            data,
            diagnostics=deletion.diagnostics,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code


def run_login(
    state: ClickState,
    *,
    json_mode: bool,
    no_color: bool,
    scope: str,
    client_id: str | None,
    no_browser: bool,
    device: bool,
    interaction: Interaction | None = None,
) -> int:
    output_json = state.json_mode or json_mode
    output_no_color = state.no_color or no_color
    interaction = interaction or ClickInteraction(open_browser=not no_browser)

    if state.no_input and not device:
        outcome = build_outcome(
            "auth.login",
            400,
            {
                "detail": "browser OAuth requires interaction; use --device in headless mode",
                "error": {
                    "code": "interactive_oauth_required",
                    "category": "usage",
                    "message": "browser OAuth requires interaction; use --device in headless mode.",
                    "retryable": False,
                    "remediation": [
                        {
                            "description": "Use device authorization.",
                            "command": ["ta", "auth", "login", "--device", "--agent"],
                        }
                    ],
                },
            },
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    try:
        metadata = discover_oauth_metadata(
            base_url=state.base_url,
            resource=state.resource,
            requester=state.requester,
        )
    except ValueError as exc:
        outcome = build_outcome(
            "auth.login",
            400,
            {"detail": str(exc)},
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    store = TokenStore(base_url=state.base_url, resource=metadata.resource)
    if device:
        return run_device_login(
            state,
            metadata=metadata,
            output_json=output_json,
            output_no_color=output_no_color,
            scope=scope,
            client_id=client_id,
            interaction=interaction,
            save_profile=store.save_profile,
        )

    try:
        callback_capture = capture_browser_callback(
            metadata=metadata,
            client_id=client_id,
            scope=scope,
            interaction=interaction,
            requester=state.requester,
        )
        status, data = exchange_callback_for_token(
            request=callback_capture.request,
            callback_value=callback_capture.callback_value,
            metadata=metadata,
            requester=state.requester,
        )
    except ValueError as exc:
        code = (
            "security_precondition"
            if "state" in str(exc).lower() or "redirect" in str(exc).lower()
            else "validation_error"
        )
        outcome = build_outcome(
            "auth.login",
            400,
            {
                "detail": str(exc),
                "error": {
                    "code": code,
                    "category": "security"
                    if code == "security_precondition"
                    else "usage",
                    "message": str(exc),
                    "retryable": False,
                },
            },
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    except Exception as exc:
        outcome = build_outcome(
            "auth.login", 500, {"detail": f"OAuth browser login failed: {exc}"}
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    if status >= 400:
        outcome = build_outcome("auth.login", status, data)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    try:
        profile = profile_from_token_response(
            base_url=state.base_url,
            metadata=metadata,
            client_id=callback_capture.request.client_id,
            token_data=data,
            auth_kind="pkce",
        )
    except ValueError as exc:
        outcome = build_outcome(
            "auth.login",
            400,
            {"detail": str(exc)},
            exit_code=EXIT_VALIDATION,
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    try:
        diagnostics = _save_profile(store, profile)
    except (OSError, RuntimeError) as exc:
        outcome = build_outcome(
            "auth.login",
            500,
            {"detail": str(exc), "token_saved": False},
            diagnostics=[
                CliDiagnostic(
                    level="error",
                    code="profile_storage_error",
                    category="security",
                    message=str(exc),
                    retryable=False,
                )
            ],
        )
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    outcome = build_outcome(
        "auth.login",
        status,
        {
            "authenticated": True,
            "token_saved": True,
            "auth_kind": "pkce",
            "scope": profile.scope,
            "resource": profile.resource,
        },
        diagnostics=diagnostics,
    )
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code


def _save_profile(store: TokenStore, profile: AuthProfile) -> list[CliDiagnostic]:
    diagnostics = store.save_profile(profile)
    return diagnostics if isinstance(diagnostics, list) else []
