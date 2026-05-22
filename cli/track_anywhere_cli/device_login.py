from __future__ import annotations

from .click_common import ClickState
from .config import CliConfig
from .exit_codes import EXIT_VALIDATION
from .interaction import Interaction, inform
from .oauth_login import DEFAULT_WEB_URL, create_device_login_request, exchange_device_code_for_token
from .renderers import emit_outcome
from .runtime import build_outcome


def run_device_login(
    state: ClickState,
    *,
    output_json: bool,
    output_no_color: bool,
    scope: str,
    client_id: str,
    interaction: Interaction,
    save_token,
) -> int:
    config = CliConfig(base_url=state.base_url or DEFAULT_WEB_URL, token=None, insecure_automation=state.insecure_automation)
    status, data, request = create_device_login_request(config=config, requester=state.requester, client_id=client_id, scope=scope)
    if request is None:
        outcome = build_outcome("auth.login", status, data)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    if state.no_input:
        outcome = build_outcome("auth.login", 200, {"device_authorization": data})
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    inform(interaction, "Open this URL on any device to authorize Track Anywhere CLI:")
    inform(interaction, request.verification_uri_complete)
    inform(interaction, f"Code: {request.user_code}")
    status, token_data = exchange_device_code_for_token(request=request, config=config, requester=state.requester)
    if status >= 400:
        outcome = build_outcome("auth.login", status, token_data)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        outcome = build_outcome("auth.login", 400, {"detail": "token endpoint did not return an access token"}, exit_code=EXIT_VALIDATION)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    diagnostics = save_token(access_token)
    outcome = build_outcome(
        "auth.login",
        status,
        {"authenticated": True, "token_saved": True, "auth_kind": "device", "scope": token_data.get("scope")},
        diagnostics=diagnostics,
    )
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code
