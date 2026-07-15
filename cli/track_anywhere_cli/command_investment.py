from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import quote

from .command_catalog import compact_payload
from .command_definition import CommandDefinition, Requester, api_command
from .config import CliConfig, command_idempotency_key


@api_command("investment.acquire", mutating=True, idempotent=True)
def request_acquire_lot(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    payload = compact_payload(
        {
            "command_id": args.command_id,
            "transaction_id": args.transaction_id,
            "lot_id": args.lot_id,
            "instrument_asset_code": args.instrument_asset_code,
            "settlement_asset_code": args.settlement_asset_code,
            "quantity_units": args.quantity_units,
            "cost_units": args.cost_units,
            "effective_at": args.effective_at,
            "fee_units": args.fee_units,
            "expected_stream_version": args.expected_stream_version,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/investments/lots/acquire",
        payload,
        command_idempotency_key(args, "v2-investment-acquire"),
    )


@api_command("investment.dispose", mutating=True, idempotent=True)
def request_dispose_lot(
    args: Namespace,
    config: CliConfig,
    requester: Requester,
) -> tuple[int, Any]:
    try:
        specific_lots = [_specific_lot(value) for value in args.specific_lot]
    except ValueError as error:
        return 422, {
            "detail": str(error),
            "error": {
                "code": "invalid_v2_cli_input",
                "category": "validation",
                "message": str(error),
                "retryable": False,
            },
        }
    payload = compact_payload(
        {
            "command_id": args.command_id,
            "transaction_id": args.transaction_id,
            "instrument_asset_code": args.instrument_asset_code,
            "settlement_asset_code": args.settlement_asset_code,
            "quantity_units": args.quantity_units,
            "proceeds_units": args.proceeds_units,
            "allocation_method": args.allocation_method,
            "effective_at": args.effective_at,
            "fee_units": args.fee_units,
            "specific_lots": specific_lots,
            "expected_stream_version": args.expected_stream_version,
        }
    )
    return requester(
        config,
        "POST",
        f"/api/v2/books/{_path(args.book_id)}/investments/lots/dispose",
        payload,
        command_idempotency_key(args, "v2-investment-dispose"),
    )


def _specific_lot(raw: str) -> dict[str, str]:
    parts = raw.split(":", 1)
    if len(parts) != 2 or any(not value for value in parts):
        raise ValueError("--specific-lot must be LOT_ID:QUANTITY_UNITS")
    return {"lot_id": parts[0], "quantity_units": parts[1]}


def _path(value: object) -> str:
    return quote(str(value), safe="")


INVESTMENT_COMMAND_DEFINITIONS: tuple[CommandDefinition, ...] = (
    request_acquire_lot,
    request_dispose_lot,
)
