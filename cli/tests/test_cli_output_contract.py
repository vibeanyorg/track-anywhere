from __future__ import annotations

import json

from rich.table import Table

from track_anywhere_cli.output import CliDiagnostic, CliOutcome, outcome_to_json_document
from track_anywhere_cli.exit_codes import EXIT_SUCCESS, EXIT_AUTH
from track_anywhere_cli.runtime import build_outcome
from track_anywhere_cli.presenters import presenter_for


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


def test_build_outcome_maps_status_to_exit_code():
    outcome = build_outcome("account.show", 404, {"detail": "missing"})

    assert outcome.command_path == "account.show"
    assert outcome.status == 404
    assert outcome.exit_code == 8
    assert outcome.ok is False


def test_account_list_has_explicit_presenter():
    presenter = presenter_for("account.list")
    renderable = presenter({"accounts": []})

    assert not isinstance(renderable, dict)


def test_unknown_presenter_fails():
    import pytest

    with pytest.raises(KeyError):
        presenter_for("unknown.command")


def test_render_json_writes_one_envelope(capsys):
    from track_anywhere_cli.renderers import emit_outcome

    outcome = CliOutcome(
        command_path="account.list",
        status=200,
        data={"accounts": []},
        diagnostics=[],
        exit_code=0,
    )

    emit_outcome(outcome, json_mode=True, no_color=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == 200
    assert payload["diagnostics"] == []
    assert payload["command"] == "account.list"
    assert payload["data"] == {"accounts": []}


def test_diagnostics_for_known_error_statuses():
    cases = [
        (400, "security_precondition", "Validation failed"),
        (401, "auth_required", "Missing credentials"),
        (403, "policy_denied", "Access denied"),
        (409, "conflict", "Conflict"),
        (503, "request_failed", "Server unavailable"),
    ]

    for status, code, detail in cases:
        outcome = build_outcome("account.show", status, {"detail": detail})
        assert outcome.diagnostics, f"expected diagnostics for status {status}"
        assert outcome.diagnostics[0].code == code
        assert str(detail) in outcome.diagnostics[0].message
