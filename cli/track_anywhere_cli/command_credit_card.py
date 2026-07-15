from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import quote

from .command_catalog import compact_payload
from .command_definition import CommandDefinition, Requester, api_command
from .config import CliConfig, command_idempotency_key
from .structured_inputs import parse_external_reference


@api_command("card.charge", mutating=True, idempotent=True)
def request_charge(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return _request_card_transaction(
        args,
        config,
        requester,
        route="charges",
        idempotency_prefix="v2-card-charge",
        counter_field="expense_account_id",
    )


@api_command("card.payment", mutating=True, idempotent=True)
def request_payment(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return _request_card_transaction(
        args,
        config,
        requester,
        route="payments",
        idempotency_prefix="v2-card-payment",
        counter_field="source_account_id",
    )


@api_command("card.refund", mutating=True, idempotent=True)
def request_refund(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return _request_card_transaction(
        args,
        config,
        requester,
        route="refunds",
        idempotency_prefix="v2-card-refund",
        counter_field="original_transaction_id",
    )


@api_command("card.fee", mutating=True, idempotent=True)
def request_fee(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    return _request_card_transaction(
        args,
        config,
        requester,
        route="fees",
        idempotency_prefix="v2-card-fee",
        counter_field="expense_account_id",
    )


def _request_card_transaction(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
    *,
    route: str,
    idempotency_prefix: str,
    counter_field: str,
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
            "card_account_id": args.card_account_id,
            counter_field: getattr(args, counter_field),
            "asset_code": args.asset_code,
            "amount": args.amount,
            "effective_at": args.effective_at,
            "description_ref": args.description_ref,
            "external_references": references,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/credit-cards/{route}",
        payload,
        command_idempotency_key(args, idempotency_prefix),
    )


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


CREDIT_CARD_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    request_charge,
    request_payment,
    request_refund,
    request_fee,
)
