from __future__ import annotations

import json

from track_anywhere_cli.output import CliDiagnostic, CliOutcome, outcome_to_json_document
from track_anywhere_cli.exit_codes import EXIT_SUCCESS, EXIT_AUTH


def test_success_outcome_json_envelope():
    outcome = CliOutcome(
        command_path="account.list",
        status=200,
        data={"accounts": []},
        diagnostics=[],
        exit_code=EXIT_SUCCESS,
    )

    payload = json.loads(outcome_to_json_document(outcome))

    assert payload == {
        "ok": True,
        "command": "account.list",
        "status": 200,
        "data": {"accounts": []},
        "diagnostics": [],
    }


def test_error_outcome_json_envelope():
    outcome = CliOutcome(
        command_path="auth.status",
        status=401,
        data={"detail": "not authenticated"},
        diagnostics=[
            CliDiagnostic(
                level="error",
                message="Authentication is required.",
                code="auth_required",
            )
        ],
        exit_code=EXIT_AUTH,
    )

    payload = json.loads(outcome_to_json_document(outcome))

    assert payload["ok"] is False
    assert payload["command"] == "auth.status"
    assert payload["diagnostics"][0]["code"] == "auth_required"


def test_diagnostic_to_json_omits_optional_fields_when_not_set():
    diagnostic = CliDiagnostic(level="info", message="Cache warmed")

    assert diagnostic.to_json() == {
        "level": "info",
        "message": "Cache warmed",
    }


def test_diagnostic_to_json_includes_optional_fields_when_set():
    diagnostic = CliDiagnostic(
        level="warning",
        message="Rate limit nears",
        code="rate_limit_warning",
        detail={"retry_after": 30},
    )

    assert diagnostic.to_json() == {
        "level": "warning",
        "message": "Rate limit nears",
        "code": "rate_limit_warning",
        "detail": {"retry_after": 30},
    }
