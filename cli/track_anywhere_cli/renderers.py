from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table


def emit_result(data: Any, *, json_mode: bool, no_color: bool, command_path: str = "") -> None:
    if json_mode:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    console = Console(no_color=no_color)
    renderable = _render_human(data, command_path)
    console.print(renderable)


def _render_human(data: Any, command_path: str):
    if isinstance(data, str):
        return data
    if command_path == "recurring.reminders" and isinstance(data, dict):
        return _recurring_reminders_table(data.get("reminders", []))
    if command_path == "recurring.list" and isinstance(data, dict):
        return _recurring_items_table(data.get("recurring_items", []))
    if command_path == "recurring.draft_due" and isinstance(data, dict):
        return _recurring_drafts_table(data.get("result", data))
    return data


def _recurring_reminders_table(reminders: list[dict[str, Any]]) -> Table:
    table = Table(title="Recurring reminders")
    table.add_column("Name")
    table.add_column("Provider")
    table.add_column("Renewal")
    table.add_column("Reminder")
    table.add_column("Lead", justify="right")
    table.add_column("Amount", justify="right")
    for item in reminders:
        amount = ""
        if item.get("amount") is not None:
            amount = f"{item['amount']} {item.get('currency') or ''}".strip()
        table.add_row(
            str(item.get("name") or ""),
            str(item.get("provider") or ""),
            str(item.get("renewal_date") or ""),
            str(item.get("reminder_date") or ""),
            str(item.get("lead_days") or ""),
            amount,
        )
    return table


def _recurring_items_table(items: list[dict[str, Any]]) -> Table:
    table = Table(title="Recurring items")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Kind")
    table.add_column("Provider")
    table.add_column("Amount", justify="right")
    for item in items:
        amount = ""
        if item.get("amount") is not None:
            amount = f"{item['amount']} {item.get('currency') or ''}".strip()
        table.add_row(
            str(item.get("name") or ""),
            str(item.get("status") or ""),
            str(item.get("kind") or ""),
            str(item.get("provider") or ""),
            amount,
        )
    return table


def _recurring_drafts_table(result: dict[str, Any]) -> Table:
    table = Table(title=f"Recurring draft run {result.get('as_of', '')}".strip())
    table.add_column("Action")
    table.add_column("Name")
    table.add_column("Renewal")
    table.add_column("Draft")
    for item in result.get("created", []):
        table.add_row("created", str(item.get("recurring_id") or ""), str(item.get("renewal_date") or ""), str(item.get("draft_id") or ""))
    for item in result.get("skipped", []):
        table.add_row(str(item.get("reason") or "skipped"), str(item.get("name") or ""), str(item.get("renewal_date") or ""), str(item.get("last_draft_id") or ""))
    return table
