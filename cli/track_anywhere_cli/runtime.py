from __future__ import annotations

from typing import Any

from .exit_codes import EXIT_SUCCESS
from .http import exit_for_status
from .output import CliDiagnostic, CliOutcome


def diagnostics_for_status(status: int, data: Any) -> list[CliDiagnostic]:
    if status < 400:
        return []

    code = {
        401: "auth_required",
        403: "policy_denied",
        404: "not_found",
        409: "conflict",
        400: "security_precondition",
    }.get(status, "request_failed")

    detail = data.get("detail") if isinstance(data, dict) else data

    return [
        CliDiagnostic(
            level="error",
            code=code,
            message=str(detail or "Command failed."),
            detail=data,
        )
    ]


def build_outcome(
    command_path: str,
    status: int,
    data: Any,
    diagnostics: list[CliDiagnostic] | None = None,
) -> CliOutcome:
    all_diagnostics = [*diagnostics_for_status(status, data), *(diagnostics or [])]
    return CliOutcome(
        command_path=command_path,
        status=status,
        data=data,
        diagnostics=all_diagnostics,
        exit_code=EXIT_SUCCESS if status < 400 else exit_for_status(status, data),
    )
