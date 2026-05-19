from __future__ import annotations

from argparse import Namespace
import json
from urllib.parse import parse_qs, urlparse

from rich.console import Console

import pytest

from track_anywhere_cli.commands import command_paths, command_spec, command_specs
from track_anywhere_cli.config import CliConfig
from track_anywhere_cli.output import CliDiagnostic, CliOutcome, outcome_to_json_document
from track_anywhere_cli.exit_codes import EXIT_SUCCESS, EXIT_AUTH
from track_anywhere_cli.runtime import RuntimeContext, build_outcome
from track_anywhere_cli.presenters import PRESENTERS, presenter_for


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


def test_public_command_paths_have_presenters():
    for command_path in (
        "capture",
        "draft.confirm",
        "tx.record",
        "tx.list",
        "tx.show",
        "tx.reverse",
        "expense.record",
        "income.record",
        "balance.adjust",
        "balance",
        "investment.event",
        "investment.performance",
        "recurring.create",
        "recurring.list",
        "recurring.show",
        "recurring.update",
        "recurring.reminders",
        "recurring.draft_due",
        "summary.accounts",
        "summary.categories",
        "user.create",
        "user.list",
        "category.create",
        "category.list",
        "category.find",
        "category.show",
        "credit_card.list",
        "credit_card.show",
        "credit_card.update",
        "account.create",
        "account.list",
        "account.find",
        "account.show",
        "account.update",
        "account.balance",
        "account.adjust",
    ):
        renderable = presenter_for(command_path)({"status": "ok"})

        assert not isinstance(renderable, dict)


def test_public_command_paths_include_known_contract_paths():
    paths = set(command_paths())

    assert "account.list" in paths
    assert "tx.record" in paths
    assert "auth.login" in paths
    assert set(PRESENTERS) <= paths
    assert paths <= set(PRESENTERS)
    assert "auth.login" not in command_specs()


def test_command_spec_rejects_local_only_command_paths():
    with pytest.raises(KeyError):
        command_spec("auth.login")


def test_command_spec_executes_existing_api_dispatch():
    calls = []

    def requester(config, method, path, payload=None, key=None):
        calls.append({"method": method, "path": path, "payload": payload, "key": key, "token": config.token})
        return 200, {"accounts": []}

    spec = command_spec("account.list")
    result = spec.execute(
        Namespace(
            command="account",
            account_command="list",
            name="Visa",
            type=None,
            currency="USD",
            institution_type=None,
            subtype=None,
            institution=None,
        ),
        RuntimeContext(
            config=CliConfig(base_url="http://api.test", token="token-1"),
            requester=requester,
        ),
    )

    assert spec.command_path == "account.list"
    assert spec.requires_auth is True
    assert result.status == 200
    assert result.data == {"accounts": []}
    assert calls[0]["method"] == "GET"
    assert calls[0]["payload"] is None
    assert calls[0]["key"] is None
    assert calls[0]["token"] == "token-1"
    parsed_path = urlparse(calls[0]["path"])
    assert parsed_path.path == "/api/v1/accounts"
    assert parse_qs(parsed_path.query) == {"name": ["Visa"], "currency": ["USD"]}


def test_public_presenters_render_real_payload_fields():
    cases = [
        (
            "summary.accounts",
            {"groups": [{"key": "ewallet", "currency": "USD", "asset_amount": "100", "liability_amount": "10", "net_amount": "90"}]},
            ("Account summary", "ewallet", "90"),
        ),
        (
            "category.create",
            {"category": {"category_id": "cat_1", "kind": "expense", "primary": "Food", "secondary": "Delivery"}},
            ("Category", "cat_1", "Delivery"),
        ),
        (
            "user.create",
            {"user": {"user_id": "user_1", "username": "xyy", "display_name": "XYY"}},
            ("User", "user_1", "xyy"),
        ),
        (
            "tx.record",
            {
                "transaction": {
                    "transaction_id": "txn_1",
                    "memo": "Lunch",
                    "purpose": "Lunch expense",
                    "occurred_at": "2026-05-16T12:30:00+08:00",
                    "postings": [
                        {"amount": "-38", "currency": "CNY"},
                        {"amount": "38", "currency": "CNY"},
                    ],
                }
            },
            ("Transaction recorded", "txn_1", "Lunch", "Lunch expense", "38 CNY"),
        ),
        (
            "tx.list",
            {
                "transactions": [
                    {
                        "transaction_id": "txn_2",
                        "memo": "Coffee",
                        "purpose": "Morning coffee",
                        "lines": [{"amount": "6", "currency": "USD"}],
                    }
                ]
            },
            ("Transactions", "txn_2", "Coffee", "6 USD"),
        ),
        (
            "draft.confirm",
            {"transaction": {"transaction_id": "txn_3", "memo": "Confirmed draft"}, "idempotent_replay": False},
            ("Draft confirm", "txn_3", "Confirmed draft", "False"),
        ),
        (
            "tx.reverse",
            {"transaction": {"transaction_id": "txn_4", "memo": "Reversal", "postings": [{"amount": "-10", "currency": "USD"}]}},
            ("Reversed transaction", "txn_4", "Reversal", "-10 USD"),
        ),
        (
            "balance.adjust",
            {"transaction": {"transaction_id": "txn_5", "purpose": "Cash correction", "lines": [{"amount": "10", "currency": "USD"}]}},
            ("Account adjustment", "txn_5", "Cash correction", "10 USD"),
        ),
        (
            "investment.performance",
            {
                "account_id": "acc_wealth",
                "currency": "CNY",
                "holding_days": 21,
                "total_return": "120",
            },
            ("Investment performance", "acc_wealth", "21", "120"),
        ),
        (
            "recurring.create",
            {
                "recurring_item": {
                    "recurring_id": "rec_1",
                    "name": "ChatGPT",
                    "kind": "paid",
                    "recurrence": {"type": "monthly_day", "day": 15},
                }
            },
            ("Recurring item created", "rec_1", "ChatGPT", "monthly day 15"),
        ),
        (
            "recurring.update",
            {"recurring_item": {"recurring_id": "rec_2", "name": "Netflix", "status": "paused"}},
            ("Recurring item updated", "rec_2", "Netflix", "paused"),
        ),
    ]

    for command_path, payload, expected_fragments in cases:
        console = Console(record=True, width=120)
        console.print(presenter_for(command_path)(payload))
        rendered = console.export_text()

        for fragment in expected_fragments:
            assert fragment in rendered


def test_unknown_presenter_fails():
    import pytest

    with pytest.raises(KeyError):
        presenter_for("unknown.command")


def test_known_command_paths_use_registered_presenters():
    for command_path in (
        "summary.accounts",
        "category.create",
        "tx.show",
        "auth.status",
        "data.backup",
        "investment.performance",
        "recurring.show",
    ):
        renderable = presenter_for(command_path)({"status": "ok"})

        assert not isinstance(renderable, dict)
        assert hasattr(renderable, "__rich_console__")


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
