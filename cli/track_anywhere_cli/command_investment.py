from __future__ import annotations

import urllib.parse
from argparse import Namespace
from typing import Any, Callable

from .command_catalog import compact_payload
from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


def handle_investment_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    if args.command != "investment":
        return None
    if args.investment_command == "event":
        payload = compact_payload(
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
        return requester(
            config,
            "POST",
            "/api/v1/investments/events",
            payload,
            key=command_idempotency_key(args, "investment-event"),
        )
    if args.investment_command == "performance":
        path = with_query(
            f"/api/v1/investments/accounts/{urllib.parse.quote(args.account_id)}/performance",
            {"as_of": args.as_of},
        )
        return requester(config, "GET", path)
    return None
