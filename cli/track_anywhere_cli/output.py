from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


CLI_SCHEMA_VERSION = "2026-07-16"


@dataclass(frozen=True)
class CliDiagnostic:
    level: Literal["info", "warning", "error"]
    message: str
    code: str | None = None
    category: str | None = None
    retryable: bool | None = None
    remediation: list[dict[str, Any]] | None = None
    detail: Any | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "level": self.level,
            "message": self.message,
        }
        if self.code is not None:
            payload["code"] = self.code
        if self.category is not None:
            payload["category"] = self.category
        if self.retryable is not None:
            payload["retryable"] = self.retryable
        if self.remediation is not None:
            payload["remediation"] = self.remediation
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class CliOutcome:
    command_path: str
    status: int
    data: Any
    diagnostics: list[CliDiagnostic]
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.status < 400


@dataclass(frozen=True)
class CommandResult:
    status: int
    data: Any
    diagnostics: list[CliDiagnostic] | None = None


def outcome_payload(outcome: CliOutcome) -> dict[str, Any]:
    payload = {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": outcome.ok,
        "command": outcome.command_path,
        "status": outcome.status,
        "data": outcome.data,
        "diagnostics": [diagnostic.to_json() for diagnostic in outcome.diagnostics],
    }
    if not outcome.ok:
        payload["error"] = error_payload(outcome)
    return payload


def error_payload(outcome: CliOutcome) -> dict[str, Any]:
    diagnostic = next((item for item in outcome.diagnostics if item.level == "error"), None)
    if diagnostic is None:
        diagnostic = CliDiagnostic(
            level="error",
            code="command_failed",
            category="unknown",
            message="Command failed.",
            retryable=False,
            detail=outcome.data,
        )
    payload: dict[str, Any] = {
        "code": diagnostic.code or "command_failed",
        "category": diagnostic.category or _category_for_status(outcome.status),
        "message": diagnostic.message,
        "retryable": bool(diagnostic.retryable),
    }
    if diagnostic.remediation:
        payload["remediation"] = diagnostic.remediation
    if diagnostic.detail is not None:
        payload["detail"] = diagnostic.detail
    return payload


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
    if status in {408, 429} or status >= 500:
        return "external_dependency"
    return "unknown"


def outcome_to_json_document(outcome: CliOutcome) -> str:
    return json.dumps(outcome_payload(outcome), indent=2, sort_keys=True)
