from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .command_catalog import compact_payload
from .command_definition import CommandDefinition, Requester, api_command
from .config import CliConfig, command_idempotency_key
from .structured_inputs import parse_external_reference


_PAYMENT_INSTRUMENT_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://track-anywhere.dev/v2/cli/payment-instrument",
)


@api_command("card.configure", mutating=True)
def request_configure_card(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    request_id = UUID(str(args.request_id)) if args.request_id else uuid4()
    scope = f"{args.book_id}:{request_id}"
    instrument_id = uuid5(_PAYMENT_INSTRUMENT_NAMESPACE, f"{scope}:instrument")
    binding_id = uuid5(_PAYMENT_INSTRUMENT_NAMESPACE, f"{scope}:binding")
    effective_from = args.effective_from or datetime.now(UTC).isoformat()
    payload = compact_payload(
        {
            "instrument_id": str(instrument_id),
            "binding_id": str(binding_id),
            "current_name": args.name,
            "form_factor": args.form_factor,
            "network": args.network,
            "provider_code": args.provider_code,
            "settlement_policy": args.settlement_policy,
            "settlement_account_id": args.settlement_account_id,
            "asset_code": args.asset_code,
            "last4": args.last4,
            "effective_from": effective_from,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/payment-instruments",
        payload,
        None,
    )


@api_command("card.list_configured")
def request_list_configured_cards(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    from .http import with_query

    path = with_query(
        f"/api/v2/books/{_path(args.book_id)}/payment-instruments",
        {
            "status": args.status,
            "asset_code": args.asset_code,
            "name": args.name,
        },
    )
    return requester(config, "GET", path, None, None)


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
    request_configure_card,
    request_list_configured_cards,
    request_charge,
    request_payment,
    request_refund,
    request_fee,
)
