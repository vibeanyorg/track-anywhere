from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from .config import CliConfig
from .exit_codes import EXIT_SUCCESS
from .http import exit_for_status
from .output import CliDiagnostic, CliOutcome, CommandResult


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


@dataclass(frozen=True)
class RuntimeContext:
    config: CliConfig
    requester: Requester


TArg = TypeVar("TArg")


@dataclass(frozen=True)
class CliCommandSpec(Generic[TArg]):
    command_path: str
    requires_auth: bool
    execute: Callable[[TArg, RuntimeContext], CommandResult]


def diagnostics_for_status(status: int, data: Any) -> list[CliDiagnostic]:
    if status < 400:
        return []

    declared_error = data.get("error") if isinstance(data, dict) else None
    if isinstance(declared_error, dict):
        detail = data.get("detail", declared_error.get("detail"))
        return [
            CliDiagnostic(
                level="error",
                code=str(declared_error.get("code") or _code_for_status(status, detail)),
                category=str(declared_error.get("category") or _category_for_status(status)),
                message=str(declared_error.get("message") or detail or "Command failed."),
                retryable=bool(declared_error.get("retryable", _retryable_for_status(status))),
                remediation=declared_error.get("remediation") if isinstance(declared_error.get("remediation"), list) else None,
                detail=data,
            )
        ]

    detail = data.get("detail") if isinstance(data, dict) else data

    return [
        CliDiagnostic(
            level="error",
            code=_code_for_status(status, detail),
            category=_category_for_status(status),
            message=str(detail or "Command failed."),
            retryable=_retryable_for_status(status),
            detail=data,
        )
    ]


def _code_for_status(status: int, detail: Any) -> str:
    detail_text = str(detail).lower()
    if status == 401:
        return "auth_required"
    if status == 403:
        return "policy_denied"
    if status == 404:
        return "not_found"
    if status == 409 and "idempotency" in detail_text:
        return "idempotency_conflict"
    if status == 409 and "version" in detail_text:
        return "stale_version"
    if status == 409:
        return "conflict"
    if status == 400 and ("csrf" in detail_text or "origin" in detail_text or "precondition" in detail_text):
        return "security_precondition"
    if status == 400:
        return "usage_error"
    if status == 422:
        return "validation_error"
    if status in {408, 504}:
        return "timeout"
    if status >= 500:
        return "external_dependency_error"
    return "request_failed"


def _category_for_status(status: int) -> str:
    if status == 401:
        return "auth"
    if status == 403:
        return "permission"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status in {400, 422}:
        return "usage"
    if status in {408, 429, 503, 504} or status >= 500:
        return "external_dependency"
    return "unknown"


def _retryable_for_status(status: int) -> bool:
    return status in {408, 429, 500, 502, 503, 504}


def build_outcome(
    command_path: str,
    status: int,
    data: Any,
    diagnostics: list[CliDiagnostic] | None = None,
    exit_code: int | None = None,
) -> CliOutcome:
    all_diagnostics = [*diagnostics_for_status(status, data), *(diagnostics or [])]
    return CliOutcome(
        command_path=command_path,
        status=status,
        data=data,
        diagnostics=all_diagnostics,
        exit_code=exit_code if exit_code is not None else EXIT_SUCCESS if status < 400 else exit_for_status(status, data),
    )
