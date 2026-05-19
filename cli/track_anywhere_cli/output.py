from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class CliDiagnostic:
    level: Literal["info", "warning", "error"]
    message: str
    code: str | None = None
    detail: Any | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "level": self.level,
            "message": self.message,
        }
        if self.code is not None:
            payload["code"] = self.code
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
    return {
        "ok": outcome.ok,
        "command": outcome.command_path,
        "status": outcome.status,
        "data": outcome.data,
        "diagnostics": [diagnostic.to_json() for diagnostic in outcome.diagnostics],
    }


def outcome_to_json_document(outcome: CliOutcome) -> str:
    return json.dumps(outcome_payload(outcome), indent=2, sort_keys=True)
