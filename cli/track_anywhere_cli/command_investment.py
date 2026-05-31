from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
import urllib.parse

from .command_catalog import compact_payload
from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_investment_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_investment_command_path(args)
    if command_path is None:
        return None
    handler = INVESTMENT_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_investment_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "investment":
        return None
    investment_command = getattr(args, "investment_command", None)
    if investment_command in {"event", "performance"}:
        return f"investment.{investment_command}"
    return None


def request_record_investment_event(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    event_payload = compact_payload(
        {
            "account_id": args.account_id,
            "event_type": args.event_type,
            "amount": args.amount,
            "currency": args.currency,
            "occurred_at": args.occurred_at,
            "memo": args.memo,
            "units": args.units,
            "nav": args.nav,
        }
    )
    return requester(config, "POST", "/api/v1/investments/events", event_payload, key=command_idempotency_key(args, "investment-event"))


def request_investment_performance(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    account_id = urllib.parse.quote(args.account_id)
    performance_query = with_query(f"/api/v1/investments/accounts/{account_id}/performance", {"as_of": args.as_of})
    return requester(config, "GET", performance_query)


INVESTMENT_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "investment.event": request_record_investment_event,
    "investment.performance": request_investment_performance,
}
