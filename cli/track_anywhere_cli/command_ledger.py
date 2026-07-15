from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
from urllib.parse import quote

from .command_catalog import compact_payload
from .config import CliConfig, command_idempotency_key
from .http import with_query
from .structured_inputs import parse_external_reference


Requester = Callable[
    [CliConfig, str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_ledger_command(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any] | None:
    command_path = infer_ledger_command_path(args)
    if command_path is None:
        return None
    handler = LEDGER_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_ledger_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "tx":
        return None
    subcommand = getattr(args, "tx_command", None)
    if subcommand in {
        "record",
        "list",
        "show",
        "reverse",
        "correct",
        "correct-reference",
        "fx",
        "classify",
        "clear-classification",
    }:
        return f"tx.{subcommand.replace('-', '_')}"
    return None


def request_record_transaction(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    try:
        postings = [_posting(value) for value in args.posting]
        references = [
            parse_external_reference(value) for value in args.external_reference
        ]
    except ValueError as error:
        return _invalid_input(error)
    payload = compact_payload(
        {
            "command_id": args.command_id,
            "transaction_id": args.transaction_id,
            "expected_stream_version": args.expected_stream_version,
            "kind": args.kind,
            "effective_at": args.effective_at,
            "description_ref": args.description_ref,
            "external_references": references,
            "postings": postings,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/journal/transactions",
        payload,
        command_idempotency_key(args, "v2-tx-record"),
    )


def request_list_transactions(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    path = with_query(
        f"/api/v2/books/{_path(args.book_id)}/journal",
        {
            "limit": args.limit,
            "cursor": args.cursor,
            "as_of_book_position": args.as_of_book_position,
        },
    )
    return requester(config, "GET", path, None, None)


def request_show_transaction(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    path = with_query(
        (
            f"/api/v2/books/{_path(args.book_id)}/journal/transactions/"
            f"{_path(args.transaction_id)}"
        ),
        {"as_of_book_position": args.as_of_book_position},
    )
    return requester(config, "GET", path, None, None)


def request_reverse_transaction(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = compact_payload(
        {
            "command_id": args.command_id,
            "reversal_transaction_id": args.reversal_transaction_id,
            "expected_stream_version": args.expected_stream_version,
            "reason_code": args.reason_code,
            "effective_at": args.effective_at,
            "description_ref": args.description_ref,
        }
    )
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/journal/transactions/"
            f"{_path(args.transaction_id)}/reverse"
        ),
        payload,
        command_idempotency_key(args, "v2-tx-reverse"),
    )


def request_correct_transaction(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    try:
        postings = [_posting(value) for value in args.replacement_posting]
        references = [
            parse_external_reference(value)
            for value in args.replacement_external_reference
        ]
    except ValueError as error:
        return _invalid_input(error)
    replacement = compact_payload(
        {
            "transaction_id": args.replacement_transaction_id,
            "expected_stream_version": args.replacement_expected_stream_version,
            "kind": args.replacement_kind,
            "effective_at": args.replacement_effective_at,
            "description_ref": args.replacement_description_ref,
            "external_references": references,
            "postings": postings,
        }
    )
    payload = compact_payload(
        {
            "command_id": args.command_id,
            "reversal_transaction_id": args.reversal_transaction_id,
            "expected_reversal_stream_version": (args.expected_reversal_stream_version),
            "reason_code": args.reason_code,
            "reversal_effective_at": args.reversal_effective_at,
            "replacement": replacement,
            "reversal_description_ref": args.reversal_description_ref,
        }
    )
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/journal/transactions/"
            f"{_path(args.transaction_id)}/correct"
        ),
        payload,
        command_idempotency_key(args, "v2-tx-correct"),
    )


def request_correct_external_reference(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = {
        "command_id": args.command_id,
        "provider_code": args.provider_code,
        "reference_kind": args.reference_kind,
        "corrected_reference": args.corrected_reference,
        "expected_stream_version": args.expected_stream_version,
        "effective_at": args.effective_at,
    }
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/journal/transactions/"
            f"{_path(args.transaction_id)}/external-references/correct"
        ),
        payload,
        command_idempotency_key(args, "v2-tx-correct-reference"),
    )


def request_record_fx(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    try:
        references = [
            parse_external_reference(value) for value in args.external_reference
        ]
    except ValueError as error:
        return _invalid_input(error)
    payload = compact_payload(
        {
            "command_id": args.command_id,
            "transaction_id": args.transaction_id,
            "expected_stream_version": args.expected_stream_version,
            "source_account_id": args.source_account_id,
            "source_trading_account_id": args.source_trading_account_id,
            "source_asset_code": args.source_asset_code,
            "source_amount": args.source_amount,
            "target_trading_account_id": args.target_trading_account_id,
            "target_account_id": args.target_account_id,
            "target_asset_code": args.target_asset_code,
            "target_amount": args.target_amount,
            "effective_at": args.effective_at,
            "description_ref": args.description_ref,
            "external_references": references,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/journal/fx",
        payload,
        command_idempotency_key(args, "v2-tx-fx"),
    )


def request_assign_reporting_lines(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    try:
        lines = [_reporting_line(value) for value in args.line]
    except ValueError as error:
        return _invalid_input(error)
    payload = {
        "command_id": args.command_id,
        "expected_revision": args.expected_revision,
        "lines": lines,
        "effective_at": args.effective_at,
    }
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/journal/transactions/"
            f"{_path(args.transaction_id)}/reporting-lines/assign"
        ),
        payload,
        command_idempotency_key(args, "v2-tx-classify"),
    )


def request_clear_reporting_lines(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = {
        "command_id": args.command_id,
        "expected_revision": args.expected_revision,
        "effective_at": args.effective_at,
    }
    return requester(
        config,
        "POST",
        (
            f"/api/v2/books/{_path(args.book_id)}/journal/transactions/"
            f"{_path(args.transaction_id)}/reporting-lines/clear"
        ),
        payload,
        command_idempotency_key(args, "v2-tx-clear-classification"),
    )


def _posting(raw: str) -> dict[str, str]:
    parts = raw.split(":", 4)
    if len(parts) != 5 or any(not value for value in parts):
        raise ValueError(
            "--posting must be POSTING_ID:ACCOUNT_ID:ASSET_CODE:SIDE:AMOUNT"
        )
    posting_id, account_id, asset_code, side, amount = parts
    if side not in {"debit", "credit"}:
        raise ValueError("posting SIDE must be debit or credit")
    return {
        "posting_id": posting_id,
        "account_id": account_id,
        "asset_code": asset_code,
        "side": side,
        "amount": amount,
    }


def _reporting_line(raw: str) -> dict[str, str]:
    parts = raw.split(":", 8)
    if len(parts) < 7 or any(not value for value in parts[:7]):
        raise ValueError(
            "--line must be LINE_ID:LINE_VERSION_ID:CATALOG_ID:ASSET_CODE:"
            "UNITS:LINE_KIND:DIMENSION[:DIMENSION_ID[:DESCRIPTION_REF]]"
        )
    values = parts + [""] * (9 - len(parts))
    payload = {
        "line_id": values[0],
        "line_version_id": values[1],
        "catalog_id": values[2],
        "asset_code": values[3],
        "units": values[4],
        "line_kind": values[5],
        "dimension": values[6],
    }
    if values[7]:
        payload["dimension_id"] = values[7]
    if values[8]:
        payload["description_ref"] = values[8]
    return payload


def _invalid_input(error: ValueError) -> tuple[int, dict[str, Any]]:
    return 422, {
        "detail": str(error),
        "error": {
            "code": "invalid_v2_cli_input",
            "category": "validation",
            "message": str(error),
            "retryable": False,
        },
    }


def _path(value: object) -> str:
    return quote(str(value), safe="")


LEDGER_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "tx.record": request_record_transaction,
    "tx.list": request_list_transactions,
    "tx.show": request_show_transaction,
    "tx.reverse": request_reverse_transaction,
    "tx.correct": request_correct_transaction,
    "tx.correct_reference": request_correct_external_reference,
    "tx.fx": request_record_fx,
    "tx.classify": request_assign_reporting_lines,
    "tx.clear_classification": request_clear_reporting_lines,
}
