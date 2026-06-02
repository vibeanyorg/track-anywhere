from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

import click

from track_anywhere.balance_semantics import (
    ACCOUNT_TYPE_BALANCE_SEMANTICS,
    CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
    CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS,
)
from track_anywhere.posting_semantics import PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS

from .commands import LOCAL_COMMAND_PATHS, MUTATING_COMMAND_PATHS, command_paths
from .output import CLI_SCHEMA_VERSION
from .posting_semantics import (
    canonical_posting_semantics_metadata,
    posting_semantics_output_guidance,
)


CLI_PACKAGE_NAME = "track-anywhere"
API_VERSION = "v1"


COMMAND_TOKENS: dict[str, list[str]] = {
    "auth.dev_token": ["auth", "dev-token"],
    "auth.login": ["auth", "login"],
    "auth.status": ["auth", "status"],
    "balance.adjust": ["balance-adjust"],
    "capabilities": ["capabilities"],
    "credit_card.list": ["credit-card", "list"],
    "credit_card.show": ["credit-card", "show"],
    "credit_card.update": ["credit-card", "update"],
    "data.backup": ["data", "backup"],
    "draft.confirm": ["draft-confirm"],
    "recurring.draft_due": ["recurring", "draft-due"],
    "release.bump": ["release", "bump"],
    "schema": ["schema"],
    "version": ["version"],
}

POSTING_SEMANTICS_COMMAND_PATHS = frozenset(
    {
        "account.adjust",
        "account.create",
        "balance.adjust",
        "capture",
        "draft.confirm",
        "expense.record",
        "income.record",
        "investment.event",
        "recurring.create",
        "recurring.draft_due",
        "recurring.update",
        "record",
        "tx.record",
        "tx.reverse",
    }
)


def cli_version() -> str:
    try:
        return version(CLI_PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.1.0"


def version_payload() -> dict[str, Any]:
    return {
        "cli_version": cli_version(),
        "schema_version": CLI_SCHEMA_VERSION,
        "api_version": API_VERSION,
        "supports": supports_payload(),
    }


def capabilities_payload(root: click.Group) -> dict[str, Any]:
    return {
        **version_payload(),
        "commands": [_command_summary(root, command_path) for command_path in command_paths()],
    }


def schema_payload(root: click.Group, command_path: str | None = None) -> dict[str, Any]:
    if command_path:
        return {"command": command_schema(root, command_path)}
    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "commands": [_command_summary(root, path) for path in command_paths()],
    }


def command_schema(root: click.Group, command_path: str) -> dict[str, Any]:
    tokens = command_tokens(command_path)
    command = click_command(root, tokens)
    payload = {
        "command_path": command_path,
        "command": ["ta", *tokens],
        "description": command.help or command.short_help or "",
        "side_effects": side_effects(command_path),
        "idempotent": command_path in MUTATING_COMMAND_PATHS,
        "supports_dry_run": _has_option(command, "--dry-run"),
        "supports_input_stdin": False,
        "requires_auth": command_path not in LOCAL_COMMAND_PATHS,
        "arguments": [_argument_schema(param) for param in command.params if isinstance(param, click.Argument)],
        "flags": [_option_schema(param) for param in command.params if isinstance(param, click.Option)],
        "output": {"format": "CliOutcome", "schema_version": CLI_SCHEMA_VERSION},
    }
    posting_semantics = _posting_semantics_guidance(command_path)
    if posting_semantics is not None:
        payload["posting_semantics"] = posting_semantics
    posting_semantics_output = _posting_semantics_output_guidance(command_path)
    if posting_semantics_output is not None:
        payload["posting_semantics_output"] = posting_semantics_output
    credit_card_flow = _credit_card_flow_guidance(command_path)
    if credit_card_flow is not None:
        payload["credit_card_flow"] = credit_card_flow
    natural_balance_input = _natural_balance_input_guidance(command_path)
    if natural_balance_input is not None:
        payload["natural_balance_input"] = natural_balance_input
    non_posting_numeric_fields = _non_posting_numeric_guidance(command_path)
    if non_posting_numeric_fields is not None:
        payload["non_posting_numeric_fields"] = non_posting_numeric_fields
    balance_semantics = _balance_semantics_guidance(command_path)
    if balance_semantics is not None:
        payload["balance_semantics"] = balance_semantics
    return payload


def command_tokens(command_path: str) -> list[str]:
    if command_path in COMMAND_TOKENS:
        return COMMAND_TOKENS[command_path]
    parts = command_path.split(".")
    return [part.replace("_", "-") for part in parts]


def click_command(root: click.Group, tokens: list[str]) -> click.Command:
    command: click.Command = root
    for token in tokens:
        if not isinstance(command, click.Group) or token not in command.commands:
            raise KeyError(" ".join(tokens))
        command = command.commands[token]
    return command


def supports_payload() -> dict[str, Any]:
    return {
        "json_output": True,
        "format_flag": ["human", "json"],
        "agent_mode": True,
        "no_input": True,
        "structured_errors": True,
        "stderr_errors": True,
        "dry_run_commands": ["capture", "release.bump"],
        "idempotency_keys": True,
        "agent_requires_explicit_idempotency_key": True,
        "ndjson_output": False,
        "cursor_pagination": False,
    }


def side_effects(command_path: str) -> list[str]:
    if command_path not in MUTATING_COMMAND_PATHS:
        return []
    return [f"mutates:{command_path}"]


def _posting_semantics_guidance(command_path: str) -> dict[str, Any] | None:
    if command_path not in POSTING_SEMANTICS_COMMAND_PATHS:
        return None
    if command_path in {"account.adjust", "balance.adjust", "account.create"}:
        amount_rule = (
            "command balance input may be signed natural balance intent; "
            "do not persist it as a signed posting amount; postings store positive amount plus debit/credit side"
        )
    elif command_path == "draft.confirm":
        amount_rule = (
            "command has no amount input; it confirms existing draft postings, which must already use "
            "positive amount plus debit/credit side"
        )
    elif command_path == "tx.reverse":
        amount_rule = (
            "command has no amount input; it creates opposite-side debit/credit reversal postings from the "
            "stored transaction"
        )
    elif command_path == "recurring.draft_due":
        amount_rule = (
            "command has no amount input; generated draft postings come from positive recurring item amounts "
            "and use positive amount plus debit/credit side"
        )
    else:
        amount_rule = "command amount is a positive business amount; postings store positive amount plus debit/credit side"
    return {
        **canonical_posting_semantics_metadata(),
        "amount_rule": amount_rule,
        "forbidden_input_fields": list(PUBLIC_WRITE_FORBIDDEN_POSTING_FIELDS),
    }


def _posting_semantics_output_guidance(command_path: str) -> dict[str, Any] | None:
    preferred_fields_by_command = {
        "data.backup": [
            "backup.posting_semantics",
            "postgres_transaction_backup_file.posting_semantics",
        ],
        "tx.snapshot": [
            "snapshot.posting_semantics",
            "snapshot.transaction.posting_semantics",
        ],
    }
    preferred_fields = preferred_fields_by_command.get(command_path)
    if preferred_fields is None:
        return None
    return posting_semantics_output_guidance(preferred_fields)


def _credit_card_flow_guidance(command_path: str) -> dict[str, Any] | None:
    if command_path == "expense.record":
        return {
            "amount_rule": "amount is a positive expense amount",
            "credit_card_source_rule": (
                "if from_account_id is a credit-card liability account, the expense credits "
                "the liability and increases outstanding debt"
            ),
            "do_not_use_negative_amounts_or_raw_posting_signs": True,
        }
    if command_path == "tx.record":
        return {
            "amount_rule": "amount is a positive transfer amount",
            "source_target_rule": "source account is credited; target account is debited",
            "credit_card_repayment_rule": (
                "asset-to-credit-card-liability transfer is a repayment; it debits the liability "
                "and decreases outstanding debt"
            ),
            "credit_card_source_rule": (
                "credit-card liability as source credits the liability and increases outstanding debt; "
                "do not use this shape for repayments"
            ),
            "do_not_use_negative_amounts_or_raw_posting_signs": True,
        }
    return None


def _natural_balance_input_guidance(command_path: str) -> dict[str, Any] | None:
    if command_path not in {"account.adjust", "balance.adjust", "account.create"}:
        return None
    if command_path in {"account.adjust", "balance.adjust"}:
        amount_field = "amount"
        input_semantics = "signed natural account balance delta"
        liability_rule = (
            "for liability accounts, positive delta increases outstanding debt; "
            "negative delta decreases debt or creates overpayment"
        )
    else:
        amount_field = "opening_balance"
        input_semantics = "signed natural opening balance"
        liability_rule = (
            "for liability accounts, positive opening balance is initial outstanding debt; "
            "negative opening balance is initial overpayment"
        )
    return {
        "amount_field": amount_field,
        "input_semantics": input_semantics,
        "storage_model": "converted to positive debit/credit postings before persistence",
        "liability_rule": liability_rule,
        "credit_card_snapshot_rule": (
            "convert provider display to natural liability first: amount owed is positive; "
            "overpayment or credit balance is negative"
        ),
        "do_not_use_raw_posting_signs": True,
    }


def _non_posting_numeric_guidance(command_path: str) -> dict[str, Any] | None:
    fields_by_command = {
        "credit_card.update": [
            "credit_limit",
            "available_credit",
            "annual_fee",
        ],
        "payment.profile.create": [
            "settlement_rate",
        ],
        "investment.performance": [
            "total_return",
            "current_value",
            "cash_flows",
        ],
    }
    fields = fields_by_command.get(command_path)
    if fields is None:
        return None
    guidance = {
        "fields": fields,
        "rule": "these numeric fields are profile, valuation, or reporting values; they are not ledger posting amounts",
        "do_not_apply_posting_side_or_signed_amount_semantics": True,
    }
    if command_path == "credit_card.update":
        guidance["available_credit_rule"] = (
            "available_credit is optional provider-reported profile metadata; "
            "it is not a natural liability balance or posting amount, and ledger-derived availability is derived_available_credit"
        )
    return guidance


def _balance_semantics_guidance(command_path: str) -> dict[str, Any] | None:
    if command_path not in {"account.balance", "balance", "credit_card.show", "credit_card.list", "summary.accounts"}:
        return None
    preferred_fields = [
        "balance_semantics",
    ]
    if command_path in {"account.balance", "balance"}:
        preferred_fields.extend(
            [
                "official_balance.amount",
                "official_balance.amount_semantics",
                "projected_balance.amount",
                "projected_balance.amount_semantics",
                "projected_balance.pending_impact",
                "projected_balance.pending_impact_semantics",
                "liability_balance.outstanding_amount",
                "liability_balance.outstanding_amount_semantics",
                "liability_balance.overpayment_amount",
                "liability_balance.overpayment_amount_semantics",
                "projected_liability_balance.outstanding_amount",
                "projected_liability_balance.outstanding_amount_semantics",
                "projected_liability_balance.overpayment_amount",
                "projected_liability_balance.overpayment_amount_semantics",
            ]
        )
    if command_path == "credit_card.show":
        preferred_fields.extend(
            [
                "natural_balance",
                "natural_balance_semantics",
                "outstanding_balance",
                "outstanding_balance_semantics",
                "overpayment_balance",
                "overpayment_balance_semantics",
                "derived_available_credit_semantics",
                "current_balance_semantics",
            ]
        )
    if command_path == "credit_card.list":
        preferred_fields.extend(
            [
                "credit_cards.natural_balance",
                "credit_cards.natural_balance_semantics",
                "credit_cards.outstanding_balance",
                "credit_cards.outstanding_balance_semantics",
                "credit_cards.overpayment_balance",
                "credit_cards.overpayment_balance_semantics",
                "credit_cards.derived_available_credit_semantics",
                "credit_cards.current_balance_semantics",
            ]
        )
    compatibility_aliases: dict[str, str] = {}
    if command_path == "credit_card.show":
        compatibility_aliases["current_balance"] = CREDIT_CARD_CURRENT_BALANCE_COMPATIBILITY_ALIAS
    if command_path == "credit_card.list":
        compatibility_aliases["credit_cards.current_balance"] = CREDIT_CARD_COLLECTION_CURRENT_BALANCE_COMPATIBILITY_ALIAS
    if command_path == "summary.accounts":
        preferred_fields.extend(
            [
                "summary_semantics",
                "groups.amount_semantics",
                "groups.fund_amount",
                "groups.fund_amount_semantics",
                "groups.net_amount",
                "groups.liability_outstanding_amount",
                "groups.liability_overpayment_amount",
                "groups.liability_outstanding_amount_semantics",
                "groups.liability_overpayment_amount_semantics",
                "groups.net_amount_semantics",
            ]
        )
    guidance = {
        "liability_balance_rule": "for liability accounts, positive natural balance means outstanding debt and negative natural balance means overpayment",
        "account_type_balance_semantics": ACCOUNT_TYPE_BALANCE_SEMANTICS,
        "preferred_fields": preferred_fields,
        "do_not_infer_from_sign": True,
    }
    if compatibility_aliases:
        guidance["compatibility_aliases"] = compatibility_aliases
    return guidance


def _command_summary(root: click.Group, command_path: str) -> dict[str, Any]:
    try:
        schema = command_schema(root, command_path)
    except KeyError:
        return {"command_path": command_path, "registered": False}
    return {
        "command_path": schema["command_path"],
        "command": schema["command"],
        "registered": True,
        "side_effects": schema["side_effects"],
        "supports_dry_run": schema["supports_dry_run"],
        "requires_auth": schema["requires_auth"],
    }


def _argument_schema(argument: click.Argument) -> dict[str, Any]:
    return {
        "name": argument.name,
        "required": argument.required,
        "nargs": argument.nargs,
        "type": argument.type.name,
    }


def _option_schema(option: click.Option) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": option.name,
        "opts": list(option.opts),
        "secondary_opts": list(option.secondary_opts),
        "required": option.required,
        "is_flag": option.is_flag,
        "multiple": option.multiple,
        "type": option.type.name,
        "help": option.help or "",
    }
    if isinstance(option.type, click.Choice):
        payload["choices"] = list(option.type.choices)
    default = _jsonable_default(option.default)
    if default is not None:
        payload["default"] = default
    return payload


def _has_option(command: click.Command, option_name: str) -> bool:
    return any(isinstance(param, click.Option) and option_name in param.opts for param in command.params)


def _jsonable_default(value: Any) -> Any | None:
    if value in (None, ()):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return None
