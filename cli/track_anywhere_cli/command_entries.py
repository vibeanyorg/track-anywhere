from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .command_catalog import compact_payload
from .command_definition import (
    CommandDefinition,
    Requester,
    api_command,
)
from .config import CliConfig
from .structured_inputs import parse_external_reference


PREPARE_ENTRY_PATH = "/api/v2/books/{book_id}/entries/prepare"
COMMIT_ENTRY_PATH = "/api/v2/books/{book_id}/entries/commit"
ENTRY_COMMAND_PATHS = (
    "expense",
    "income",
    "transfer",
    "card_pay",
    "refund",
    "reconcile",
)


def _prepare_definition(command_path: str) -> CommandDefinition:
    @api_command(command_path, mutating=True)
    def prepare(
        args: Namespace,
        config: CliConfig,
        requester: Requester,
    ) -> tuple[int, Any]:
        return request_prepare_entry(args, config, requester)

    return prepare


ENTRY_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = tuple(
    _prepare_definition(command_path)
    for command_path in ENTRY_COMMAND_PATHS
)


def request_prepare_entry(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    try:
        payload = build_entry_payload(args)
    except ValueError as error:
        return _invalid_input(error)
    return requester(
        config,
        "POST",
        PREPARE_ENTRY_PATH.format(book_id=args.book_id),
        payload,
        None,
    )


def request_commit_entry(
    *,
    book_id: str,
    intent_id: str,
    commit_token: str,
    request_id: str,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = {
        "intent_id": intent_id,
        "commit_token": commit_token,
        "request_id": request_id,
    }
    return requester(
        config,
        "POST",
        COMMIT_ENTRY_PATH.format(book_id=book_id),
        payload,
        request_id,
    )


def build_entry_payload(args: Namespace) -> dict[str, Any]:
    builders = {
        "expense": _build_expense,
        "income": _build_income,
        "transfer": _build_transfer,
        "credit_card_payment": _build_credit_card_payment,
        "refund": _build_refund,
        "adjustment": _build_adjustment,
    }
    try:
        builder = builders[args.entry_kind]
    except (AttributeError, KeyError) as error:
        raise ValueError("unsupported everyday entry kind") from error
    return compact_payload(builder(args))


def new_request_id() -> str:
    return str(uuid4())


def _build_expense(args: Namespace) -> dict[str, Any]:
    source_account, payment_instrument = _payment_source(args)
    return {
        "kind": "expense",
        "amount": _money(args),
        "source_account": source_account,
        "payment_instrument": payment_instrument,
        "category": _category_ref(args.category),
        "occurred_at": _occurred_at(args.occurred_at),
        "narrative": _narrative(args),
    }


def _build_income(args: Namespace) -> dict[str, Any]:
    return {
        "kind": "income",
        "amount": _money(args),
        "destination_account": _account_ref(
            args.destination_account,
            last4=args.destination_last4,
            subtype=args.destination_subtype,
        ),
        "category": _category_ref(args.category),
        "occurred_at": _occurred_at(args.occurred_at),
        "narrative": _narrative(args),
    }


def _build_transfer(args: Namespace) -> dict[str, Any]:
    return {
        "kind": "transfer",
        "amount": _money(args),
        "source_account": _account_ref(
            args.source_account,
            last4=args.source_last4,
            subtype=args.source_subtype,
        ),
        "destination_account": _account_ref(
            args.destination_account,
            last4=args.destination_last4,
            subtype=args.destination_subtype,
        ),
        "occurred_at": _occurred_at(args.occurred_at),
        "narrative": _narrative(args),
    }


def _build_credit_card_payment(args: Namespace) -> dict[str, Any]:
    card_account, payment_instrument = _card_payment_target(args)
    return {
        "kind": "credit_card_payment",
        "amount": _money(args),
        "funding_account": _account_ref(
            args.funding_account,
            last4=args.funding_last4,
            subtype=args.funding_subtype,
        ),
        "card_account": card_account,
        "payment_instrument": payment_instrument,
        "occurred_at": _occurred_at(args.occurred_at),
        "narrative": _narrative(args),
    }


def _payment_source(
    args: Namespace,
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    account = getattr(args, "source_account", None)
    instrument = getattr(args, "payment_instrument", None)
    if (account is None) == (instrument is None):
        raise ValueError("use exactly one of --from or --instrument")
    if account is not None:
        return (
            _account_ref(
                account,
                last4=args.source_last4,
                subtype=args.source_subtype,
            ),
            None,
        )
    return None, _payment_instrument_ref(
        instrument,
        last4=args.instrument_last4,
        provider_code=args.instrument_provider,
    )


def _card_payment_target(
    args: Namespace,
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    account = getattr(args, "card_account", None)
    instrument = getattr(args, "payment_instrument", None)
    if (account is None) == (instrument is None):
        raise ValueError("use exactly one of --card or --instrument")
    if account is not None:
        return (
            _account_ref(
                account,
                last4=args.card_last4,
                subtype=args.card_subtype,
            ),
            None,
        )
    return None, _payment_instrument_ref(
        instrument,
        last4=args.instrument_last4,
        provider_code=args.instrument_provider,
    )


def _build_refund(args: Namespace) -> dict[str, Any]:
    _require_uuid(args.original_transaction_id, label="--original")
    if args.amount is None and args.source_text is not None:
        raise ValueError("--source-text requires an explicit refund amount")
    return {
        "kind": "refund",
        "original_transaction_id": args.original_transaction_id,
        "amount": _money(args) if args.amount is not None else None,
        "occurred_at": _occurred_at(args.occurred_at),
        "narrative": _narrative(args),
    }


def _build_adjustment(args: Namespace) -> dict[str, Any]:
    return {
        "kind": "adjustment",
        "account": _account_ref(
            args.account,
            last4=args.account_last4,
            subtype=args.account_subtype,
        ),
        "actual_balance": _money(args, value=args.actual_balance),
        "occurred_at": _occurred_at(args.occurred_at),
        "narrative": _narrative(args),
    }


def _money(args: Namespace, *, value: str | None = None) -> dict[str, str]:
    exact_value = args.amount if value is None else value
    if not isinstance(exact_value, str) or not exact_value:
        raise ValueError("amount must be a non-empty exact decimal string")
    return {
        "value": exact_value,
        "denomination": args.denomination,
        "asset_code": args.asset_code,
        "source_text": args.source_text or exact_value,
    }


def _account_ref(
    raw: str,
    *,
    last4: str | None,
    subtype: str | None,
) -> dict[str, str]:
    value = raw.strip()
    if not value:
        raise ValueError("account reference must be nonblank")
    if value.startswith("id:"):
        account_id = value.removeprefix("id:")
        _require_uuid(account_id, label="account id")
        if last4 is not None or subtype is not None:
            raise ValueError("account ID references cannot include query hints")
        return {"account_id": account_id}
    return compact_payload({"query": value, "last4": last4, "subtype": subtype})


def _payment_instrument_ref(
    raw: str,
    *,
    last4: str | None,
    provider_code: str | None,
) -> dict[str, str]:
    value = raw.strip()
    if not value:
        raise ValueError("payment instrument reference must be nonblank")
    if value.startswith("id:"):
        instrument_id = value.removeprefix("id:")
        _require_uuid(instrument_id, label="payment instrument id")
        if last4 is not None or provider_code is not None:
            raise ValueError(
                "payment instrument ID references cannot include query hints"
            )
        return {"instrument_id": instrument_id}
    return compact_payload(
        {
            "query": value,
            "last4": last4,
            "provider_code": provider_code,
        }
    )


def _category_ref(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if not value:
        raise ValueError("category reference must be nonblank")
    if value.startswith("id:"):
        category_id = value.removeprefix("id:")
        _require_uuid(category_id, label="category id")
        return {"category_id": category_id}
    if "/" in value:
        path = [part.strip() for part in value.split("/")]
        if any(not part for part in path):
            raise ValueError("category path components must be nonblank")
        return {"path": path}
    return {"query": value}


def _narrative(args: Namespace) -> dict[str, Any] | None:
    external_reference = getattr(args, "external_reference", None)
    parsed_reference = (
        parse_external_reference(external_reference)
        if external_reference is not None
        else None
    )
    payload = compact_payload(
        {
            "merchant": getattr(args, "merchant", None),
            "channel": getattr(args, "channel", None),
            "note": getattr(args, "note", None),
            "external_reference": parsed_reference,
        }
    )
    return payload or None


def _occurred_at(value: str) -> str:
    if value == "now":
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _require_uuid(value: str, *, label: str) -> None:
    try:
        UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a UUID") from error


def _invalid_input(error: ValueError) -> tuple[int, dict[str, Any]]:
    message = str(error)
    return 422, {
        "detail": message,
        "error": {
            "code": "invalid_v2_cli_input",
            "category": "usage",
            "message": message,
            "retryable": False,
        },
    }


__all__ = [
    "COMMIT_ENTRY_PATH",
    "ENTRY_COMMAND_DEFINITIONS",
    "ENTRY_COMMAND_PATHS",
    "PREPARE_ENTRY_PATH",
    "build_entry_payload",
    "new_request_id",
    "request_commit_entry",
    "request_prepare_entry",
]
