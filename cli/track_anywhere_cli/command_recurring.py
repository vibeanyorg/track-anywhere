from __future__ import annotations

from argparse import Namespace
from typing import Any, Callable
import urllib.parse

from .config import CliConfig, command_idempotency_key
from .http import with_query


Requester = Callable[[CliConfig, str, str, dict[str, Any] | None, str | None], tuple[int, Any]]
CommandHandler = Callable[[Namespace, CliConfig, Requester], tuple[int, Any]]


def handle_recurring_command(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any] | None:
    command_path = infer_recurring_command_path(args)
    if command_path is None:
        return None
    handler = RECURRING_COMMAND_HANDLERS.get(command_path)
    if handler is None:
        return None
    return handler(args, config, requester)


def infer_recurring_command_path(args: Namespace) -> str | None:
    if getattr(args, "command", None) != "recurring":
        return None
    recurring_command = getattr(args, "recurring_command", None)
    if recurring_command == "draft-due":
        return "recurring.draft_due"
    if recurring_command in {"create", "list", "show", "update", "reminders"}:
        return f"recurring.{recurring_command}"
    return None


def request_create_recurring_item(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    return requester(
        config,
        "POST",
        "/api/v1/recurring/items",
        _create_payload(args),
        key=command_idempotency_key(args, "recurring-create"),
    )


def request_list_recurring_items(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    recurring_query = with_query("/api/v1/recurring/items", {"status": args.status, "kind": args.kind})
    return requester(config, "GET", recurring_query)


def request_show_recurring_item(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    recurring_id = urllib.parse.quote(args.recurring_id)
    return requester(config, "GET", f"/api/v1/recurring/items/{recurring_id}")


def request_update_recurring_item(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    update_payload = {}
    if args.status:
        update_payload["status"] = args.status
    if args.reminder_days:
        update_payload["reminder_days"] = args.reminder_days
    recurring_id = urllib.parse.quote(args.recurring_id)
    return requester(config, "PATCH", f"/api/v1/recurring/items/{recurring_id}", update_payload, key=command_idempotency_key(args, "recurring-update"))


def request_recurring_reminders(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    reminder_query = with_query("/api/v1/recurring/reminders", {"as_of": args.as_of, "window_days": args.window_days})
    return requester(config, "GET", reminder_query)


def request_draft_due_recurring_items(args: Namespace, config: CliConfig, requester: Requester) -> tuple[int, Any]:
    draft_payload = {"as_of": args.as_of} if args.as_of else {}
    return requester(config, "POST", "/api/v1/recurring/drafts", draft_payload, key=command_idempotency_key(args, "recurring-drafts-generate"))


def _create_payload(args: Namespace) -> dict[str, Any]:
    recurring_payload = {
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
    recurring_payload.update({key: value for key, value in optional_fields.items() if value not in (None, "")})
    return recurring_payload


def _recurrence_payload(args: Namespace) -> dict[str, Any]:
    if args.monthly_day is not None:
        return {"type": "monthly_day", "day": args.monthly_day}
    month_text, day_text = args.yearly_date.split("-", maxsplit=1)
    return {"type": "yearly_date", "month": int(month_text), "day": int(day_text)}


RECURRING_COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "recurring.create": request_create_recurring_item,
    "recurring.list": request_list_recurring_items,
    "recurring.show": request_show_recurring_item,
    "recurring.update": request_update_recurring_item,
    "recurring.reminders": request_recurring_reminders,
    "recurring.draft_due": request_draft_due_recurring_items,
}
