from __future__ import annotations

import json

import pytest

from track_anywhere.balance_semantics import (
    ACCOUNT_TYPE_BALANCE_SEMANTICS as BACKEND_BALANCE_SEMANTICS,
    CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
    CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
)
from track_anywhere.posting_semantics import (
    DEBIT_CREDIT_AMOUNT_RULE,
    DEBIT_CREDIT_SIDE_RULE,
    LEGACY_SIGNED_SCOPE,
    POSTING_CANONICAL_MODEL,
    POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS,
    POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS,
    PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS,
    backup_posting_semantics_metadata,
    canonical_posting_semantics_metadata,
)
from track_anywhere_cli.main import EXIT_VALIDATION, main
from track_anywhere_cli.posting_semantics import (
    DEBIT_CREDIT_AMOUNT_RULE as CLI_DEBIT_CREDIT_AMOUNT_RULE,
    DEBIT_CREDIT_SIDE_RULE as CLI_DEBIT_CREDIT_SIDE_RULE,
    LEGACY_SIGNED_SCOPE as CLI_LEGACY_SIGNED_SCOPE,
    POSTING_CANONICAL_MODEL as CLI_POSTING_CANONICAL_MODEL,
    backup_posting_semantics,
    posting_semantics_output_guidance,
)
from track_anywhere_cli.protocol import ACCOUNT_TYPE_BALANCE_SEMANTICS as CLI_BALANCE_SEMANTICS


def test_json_parse_errors_emit_structured_stderr(capsys):
    exit_code = main(["--json", "nope"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["command"] == "cli.parse"
    assert payload["error"]["code"] == "unknown_command"


def test_agent_mode_requires_explicit_idempotency_key_for_mutation(capsys):
    exit_code = main(["--token", "token-1", "--agent", "account", "create", "Agent Cash"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "idempotency_key_required"


def test_agent_login_does_not_prompt_without_token_or_callback(capsys):
    exit_code = main(["--agent", "auth", "login"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_VALIDATION
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "missing_required_input"


def test_schema_command_describes_command_protocol(capsys):
    assert main(["schema", "tx.record", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert command["command"] == ["ta", "tx", "record"]
    assert command["side_effects"] == ["mutates:tx.record"]
    assert command["posting_semantics"]["canonical_model"] == "debit_credit"
    assert command["posting_semantics"]["forbidden_input_fields"] == list(PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS)
    assert any("--idempotency-key" in flag["opts"] for flag in command["flags"])


@pytest.mark.parametrize(
    "command_path",
    [
        "account.create",
        "account.adjust",
        "balance.adjust",
        "capture",
        "draft.confirm",
        "expense.record",
        "income.record",
        "investment.event",
        "recurring.create",
        "recurring.draft_due",
        "recurring.update",
        "tx.record",
        "tx.reverse",
    ],
)
def test_schema_command_marks_posting_writers_with_debit_credit_guidance(capsys, command_path):
    assert main(["schema", command_path, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert {
        key: command["posting_semantics"][key]
        for key in canonical_posting_semantics_metadata()
    } == canonical_posting_semantics_metadata()
    assert command["posting_semantics"]["canonical_model"] == "debit_credit"
    assert command["posting_semantics"]["debit_credit_side_rule"] == DEBIT_CREDIT_SIDE_RULE
    assert command["posting_semantics"]["posting_amount_field"] == "postings.amount"
    assert command["posting_semantics"]["posting_side_field"] == "postings.side"
    assert command["posting_semantics"]["posting_amount_semantics_field"] == "postings.amount_semantics"
    if command_path in {"account.adjust", "balance.adjust", "account.create"}:
        assert command["posting_semantics"]["amount_rule"].startswith("command balance input may be signed natural balance intent")
        assert "do not persist it as a signed posting amount" in command["posting_semantics"]["amount_rule"]
    elif command_path == "draft.confirm":
        assert command["posting_semantics"]["amount_rule"].startswith("command has no amount input")
        assert "confirms existing draft postings" in command["posting_semantics"]["amount_rule"]
    elif command_path == "tx.reverse":
        assert command["posting_semantics"]["amount_rule"].startswith("command has no amount input")
        assert "opposite-side debit/credit reversal postings" in command["posting_semantics"]["amount_rule"]
    elif command_path == "recurring.draft_due":
        assert command["posting_semantics"]["amount_rule"].startswith("command has no amount input")
        assert "positive recurring item amounts" in command["posting_semantics"]["amount_rule"]
    else:
        assert command["posting_semantics"]["amount_rule"].startswith("command amount is a positive business amount")
    assert command["posting_semantics"]["forbidden_input_fields"] == list(PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS)


@pytest.mark.parametrize("command_path", ["expense.record", "tx.record"])
def test_schema_command_marks_credit_card_flow_direction(capsys, command_path):
    assert main(["schema", command_path, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["command"]["credit_card_flow"]
    assert guidance["do_not_use_negative_amounts_or_raw_posting_signs"] is True
    if command_path == "expense.record":
        assert guidance["amount_rule"] == "amount is a positive expense amount"
        assert "credits the liability" in guidance["credit_card_source_rule"]
        assert "increases outstanding debt" in guidance["credit_card_source_rule"]
    else:
        assert guidance["amount_rule"] == "amount is a positive transfer amount"
        assert guidance["source_target_rule"] == "source account is credited; target account is debited"
        assert "repayment" in guidance["credit_card_repayment_rule"]
        assert "decreases outstanding debt" in guidance["credit_card_repayment_rule"]
        assert "do not use this shape for repayments" in guidance["credit_card_source_rule"]


@pytest.mark.parametrize(
    "command_path,expected_fields",
    [
        ("data.backup", ["backup.posting_semantics", "postgres_transaction_backup_file.posting_semantics"]),
        ("tx.snapshot", ["snapshot.posting_semantics", "snapshot.transaction.posting_semantics"]),
    ],
)
def test_schema_command_marks_posting_semantics_outputs(capsys, command_path, expected_fields):
    assert main(["schema", command_path, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["command"]["posting_semantics_output"]
    assert CLI_POSTING_CANONICAL_MODEL == POSTING_CANONICAL_MODEL
    assert CLI_DEBIT_CREDIT_AMOUNT_RULE == DEBIT_CREDIT_AMOUNT_RULE
    assert CLI_DEBIT_CREDIT_SIDE_RULE == DEBIT_CREDIT_SIDE_RULE
    assert CLI_LEGACY_SIGNED_SCOPE == LEGACY_SIGNED_SCOPE
    assert backup_posting_semantics() == backup_posting_semantics_metadata()
    assert {
        key: value
        for key, value in posting_semantics_output_guidance([]).items()
        if key in canonical_posting_semantics_metadata()
    } == canonical_posting_semantics_metadata()
    assert guidance["canonical_model"] == POSTING_CANONICAL_MODEL
    assert guidance["debit_credit_amount_rule"] == DEBIT_CREDIT_AMOUNT_RULE
    assert guidance["debit_credit_side_rule"] == DEBIT_CREDIT_SIDE_RULE
    assert guidance["legacy_signed_scope"] == LEGACY_SIGNED_SCOPE
    assert guidance["do_not_infer_signed_amounts"] is True
    for field in expected_fields:
        assert field in guidance["preferred_fields"]


@pytest.mark.parametrize(
    "command_path,expected_fields",
    [
        ("credit_card.update", ["credit_limit", "available_credit", "annual_fee"]),
        ("payment.profile.create", ["settlement_rate"]),
        ("investment.performance", ["total_return", "current_value", "cash_flows"]),
    ],
)
def test_schema_command_marks_non_posting_numeric_fields(capsys, command_path, expected_fields):
    assert main(["schema", command_path, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["command"]["non_posting_numeric_fields"]
    assert guidance["rule"] == (
        "these numeric fields are profile, valuation, or reporting values; they are not ledger posting amounts"
    )
    assert guidance["do_not_apply_posting_side_or_signed_amount_semantics"] is True
    for field in expected_fields:
        assert field in guidance["fields"]
    if command_path == "credit_card.update":
        assert "not a natural liability balance or posting amount" in guidance["available_credit_rule"]
        assert "derived_available_credit" in guidance["available_credit_rule"]


@pytest.mark.parametrize(
    "command_path,amount_field,input_semantics,liability_phrase",
    [
        ("account.adjust", "amount", "signed natural account balance delta", "positive delta increases outstanding debt"),
        ("balance.adjust", "amount", "signed natural account balance delta", "positive delta increases outstanding debt"),
        ("account.create", "opening_balance", "signed natural opening balance", "positive opening balance is initial outstanding debt"),
    ],
)
def test_schema_command_marks_natural_balance_inputs_for_liabilities(
    capsys,
    command_path,
    amount_field,
    input_semantics,
    liability_phrase,
):
    assert main(["schema", command_path, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["command"]["natural_balance_input"]
    assert guidance["amount_field"] == amount_field
    assert guidance["input_semantics"] == input_semantics
    assert guidance["storage_model"] == "converted to positive debit/credit postings before persistence"
    assert liability_phrase in guidance["liability_rule"]
    assert "amount owed is positive" in guidance["credit_card_snapshot_rule"]
    assert guidance["do_not_use_raw_posting_signs"] is True
    amount_flag = next(flag for flag in payload["data"]["command"]["flags"] if f"--{amount_field.replace('_', '-')}" in flag["opts"])
    assert "Stored as debit/credit postings" in amount_flag["help"]


@pytest.mark.parametrize("command_path", ["account.balance", "balance", "credit_card.show", "credit_card.list", "summary.accounts"])
def test_schema_command_marks_balance_readers_with_liability_guidance(capsys, command_path):
    assert main(["schema", command_path, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert command["balance_semantics"]["do_not_infer_from_sign"] is True
    assert CLI_BALANCE_SEMANTICS == BACKEND_BALANCE_SEMANTICS
    assert command["balance_semantics"]["account_type_balance_semantics"] == BACKEND_BALANCE_SEMANTICS
    preferred_fields = command["balance_semantics"]["preferred_fields"]
    if command_path in {"account.balance", "balance"}:
        assert "official_balance.amount_semantics" in preferred_fields
        assert "projected_balance.amount_semantics" in preferred_fields
        assert "projected_balance.pending_impact_semantics" in preferred_fields
        assert "liability_balance.outstanding_amount" in preferred_fields
        assert "liability_balance.outstanding_amount_semantics" in preferred_fields
        assert "liability_balance.overpayment_amount" in preferred_fields
        assert "liability_balance.overpayment_amount_semantics" in preferred_fields
        assert "projected_liability_balance.outstanding_amount_semantics" in preferred_fields
        assert "projected_liability_balance.overpayment_amount_semantics" in preferred_fields
    if command_path == "credit_card.show":
        assert "natural_balance" in preferred_fields
        assert "natural_balance_semantics" in preferred_fields
        assert "outstanding_balance" in preferred_fields
        assert "outstanding_balance_semantics" in preferred_fields
        assert "overpayment_balance" in preferred_fields
        assert "overpayment_balance_semantics" in preferred_fields
        assert "derived_available_credit_semantics" in preferred_fields
        assert "current_balance_semantics" in preferred_fields
        assert (
            command["balance_semantics"]["compatibility_aliases"]["current_balance"]
            == CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS
        )
    if command_path == "credit_card.list":
        assert "credit_cards.natural_balance" in preferred_fields
        assert "credit_cards.natural_balance_semantics" in preferred_fields
        assert "credit_cards.outstanding_balance" in preferred_fields
        assert "credit_cards.outstanding_balance_semantics" in preferred_fields
        assert "credit_cards.overpayment_balance" in preferred_fields
        assert "credit_cards.overpayment_balance_semantics" in preferred_fields
        assert "credit_cards.derived_available_credit_semantics" in preferred_fields
        assert "credit_cards.current_balance_semantics" in preferred_fields
        assert (
            command["balance_semantics"]["compatibility_aliases"]["credit_cards.current_balance"]
            == CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS
        )
    if command_path == "summary.accounts":
        assert "groups.amount_semantics" in preferred_fields
        assert "groups.fund_amount" in preferred_fields
        assert "groups.net_amount" in preferred_fields
        assert "groups.liability_outstanding_amount" in preferred_fields
        assert "groups.liability_overpayment_amount" in preferred_fields
    assert "positive natural balance means outstanding debt" in command["balance_semantics"]["liability_balance_rule"]


def test_schema_command_describes_posting_semantics_resolve(capsys):
    assert main(["schema", "system.posting_semantics.resolve", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert command["command"] == ["ta", "system", "posting-semantics", "resolve"]
    assert command["side_effects"] == ["mutates:system.posting_semantics.resolve"]
    assert any("--book-id" in flag["opts"] for flag in command["flags"])
    assert any("--decision-json" in flag["opts"] for flag in command["flags"])
    decision_flag = next(flag for flag in command["flags"] if "--decision-json" in flag["opts"])
    for field in POSTING_SEMANTICS_REVIEW_DECISION_INPUT_FIELDS:
        assert field in decision_flag["help"]
    for field in POSTING_SEMANTICS_REVIEW_DECISION_DERIVED_FIELDS:
        assert field in decision_flag["help"]
    assert "amount_semantics" in decision_flag["help"]
    assert "Do not pass" in decision_flag["help"]
    assert any("--decision-file" in flag["opts"] for flag in command["flags"])
    assert any("--idempotency-key" in flag["opts"] for flag in command["flags"])


def test_schema_command_describes_posting_semantics_rewrite(capsys):
    assert main(["schema", "system.posting_semantics.rewrite", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    command = payload["data"]["command"]
    assert command["command"] == ["ta", "system", "posting-semantics", "rewrite"]
    assert command["side_effects"] == ["mutates:system.posting_semantics.rewrite"]
    assert any("--book-id" in flag["opts"] for flag in command["flags"])
    assert any("--idempotency-key" in flag["opts"] for flag in command["flags"])


def test_capabilities_command_exposes_agent_support(capsys):
    assert main(["capabilities", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["supports"]["agent_mode"] is True
    assert payload["data"]["supports"]["agent_requires_explicit_idempotency_key"] is True
    command_paths = {command["command_path"] for command in payload["data"]["commands"]}
    assert "system.posting_semantics.audit" in command_paths
    assert "system.posting_semantics.cutover_plan" in command_paths
    assert "system.posting_semantics.rewrite" in command_paths
    assert "system.posting_semantics.resolve" in command_paths
