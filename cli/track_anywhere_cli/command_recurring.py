from __future__ import annotations

import urllib.parse
from argparse import Namespace
from typing import Any, Callable

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]


def handle_recurring_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    if args.command != "recurring":
        return None
    if args.recurring_command == "create":
        return requester(
            config,
            "POST",
            "/api/v1/recurring/items",
            _create_payload(args),
            key=command_idempotency_key(args, "recurring-create"),
        )
    if args.recurring_command == "list":
        path = with_query("/api/v1/recurring/items", {"status": args.status, "kind": args.kind})
        return requester(config, "GET", path)
    if args.recurring_command == "show":
        recurring_id = urllib.parse.quote(args.recurring_id)
        return requester(config, "GET", f"/api/v1/recurring/items/{recurring_id}")
    if args.recurring_command == "update":
        payload = {}
        if args.status:
            payload["status"] = args.status
        if args.reminder_days:
            payload["reminder_days"] = args.reminder_days
        recurring_id = urllib.parse.quote(args.recurring_id)
        return requester(
            config,
            "PATCH",
            f"/api/v1/recurring/items/{recurring_id}",
            payload,
            key=command_idempotency_key(args, "recurring-update"),
        )
    if args.recurring_command == "reminders":
        path = with_query(
            "/api/v1/recurring/reminders",
            {"as_of": args.as_of, "window_days": args.window_days},
        )
        return requester(config, "GET", path)
    if args.recurring_command == "draft-due":
        payload = {}
        if args.as_of:
            payload["as_of"] = args.as_of
        return requester(
            config,
            "POST",
            "/api/v1/recurring/drafts",
            payload,
            key=command_idempotency_key(args, "recurring-drafts-generate"),
        )
    return None


def _create_payload(args: Namespace) -> dict[str, Any]:
    payload = {
        "name": args.name,
        "kind": args.kind,
        "recurrence": _recurrence_payload(args),
        "anchor_date": args.anchor_date,
        "reminder_days": args.reminder_days,
    }
    optional_fields = {
        "amount": args.amount,
        "currency": args.currency,
        "provider": args.provider,
        "reference": args.reference,
        "source_account_id": args.source_account_id,
        "category_id": args.category_id,
    }
    payload.update({key: value for key, value in optional_fields.items() if value not in (None, "")})
    return payload


def _recurrence_payload(args: Namespace) -> dict[str, Any]:
    if args.monthly_day is not None:
        return {"type": "monthly_day", "day": args.monthly_day}
    month_text, day_text = args.yearly_date.split("-", maxsplit=1)
    return {"type": "yearly_date", "month": int(month_text), "day": int(day_text)}
