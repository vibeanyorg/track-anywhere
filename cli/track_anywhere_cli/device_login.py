from __future__ import annotations

from .click_common import ClickState
from .exit_codes import EXIT_VALIDATION
from .interaction import Interaction, inform
from .oauth_login import (
    DEVICE_GRANT_TYPE,
    DEVICE_REDIRECT_PLACEHOLDER,
    OAuthMetadata,
    create_device_login_request,
    exchange_device_code_for_token,
    profile_from_token_response,
    register_public_client,
)
from .output import CliDiagnostic
from .renderers import emit_outcome
from .runtime import build_outcome


def run_device_login(
    state: ClickState,
    *,
    metadata: OAuthMetadata,
    output_json: bool,
    output_no_color: bool,
    scope: str,
    client_id: str | None,
    interaction: Interaction,
    save_profile,
) -> int:
    try:
        effective_client_id = client_id or register_public_client(
            metadata=metadata,
            redirect_uri=DEVICE_REDIRECT_PLACEHOLDER,
            scope=scope,
            requester=state.requester,
            grant_types=(DEVICE_GRANT_TYPE, "refresh_token"),
        )
        status, data, request = create_device_login_request(
            metadata=metadata,
            requester=state.requester,
            client_id=effective_client_id,
            scope=scope,
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
    if request is None:
        outcome = build_outcome("auth.login", status, data)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code

    inform(interaction, "Open this URL on any device to authorize Track Anywhere CLI:")
    inform(interaction, request.verification_uri_complete)
    inform(interaction, f"Code: {request.user_code}")
    status, token_data = exchange_device_code_for_token(
        request=request,
        metadata=metadata,
        requester=state.requester,
    )
    if status >= 400:
        outcome = build_outcome("auth.login", status, token_data)
        emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
        return outcome.exit_code
    try:
        profile = profile_from_token_response(
            base_url=state.base_url,
            metadata=metadata,
            client_id=effective_client_id,
            token_data=token_data,
            auth_kind="device",
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
        diagnostics = save_profile(profile)
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
            "auth_kind": "device",
            "scope": profile.scope,
            "resource": profile.resource,
        },
        diagnostics=diagnostics,
    )
    emit_outcome(outcome, json_mode=output_json, no_color=output_no_color)
    return outcome.exit_code
