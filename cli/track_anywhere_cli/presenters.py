from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

Presenter = Callable[[Any], Any]


def empty_panel(title: str, message: str) -> Panel:
    return Panel(message, title=title)


def account_list(data: Any) -> Table | Panel:
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    if not accounts:
        return empty_panel("Accounts", "No accounts found.")
    table = Table(title="Accounts")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Currency")
    table.add_column("Balance", justify="right")
    for account in accounts:
        table.add_row(
            str(account.get("account_id", "")),
            str(account.get("name", "")),
            str(account.get("type", "")),
            str(account.get("currency", "")),
            str(account.get("balance", account.get("current_balance", ""))),
        )
    return table


def success_panel(title: str) -> Presenter:
    def present(data: Any) -> Panel:
        return Panel(str(data), title=title)

    return present


def generic_payload_panel(title: str) -> Presenter:
    def present(data: Any) -> Panel:
        return Panel(str(data), title=title)

    return present


def recurring_reminders_table(data: Any) -> Table:
    reminders = data.get("reminders", []) if isinstance(data, dict) else []
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


def recurring_items_table(data: Any) -> Table:
    items = data.get("recurring_items", []) if isinstance(data, dict) else []
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


def recurring_drafts_table(data: Any) -> Table:
    result = data.get("result", data) if isinstance(data, dict) else {}
    table = Table(title=f"Recurring draft run {result.get('as_of', '')}".strip())
    table.add_column("Action")
    table.add_column("Name")
    table.add_column("Renewal")
    table.add_column("Draft")
    for item in result.get("created", []):
        table.add_row(
            "created",
            str(item.get("recurring_id") or ""),
            str(item.get("renewal_date") or ""),
            str(item.get("draft_id") or ""),
        )
    for item in result.get("skipped", []):
        table.add_row(
            str(item.get("reason") or "skipped"),
            str(item.get("name") or ""),
            str(item.get("renewal_date") or ""),
            str(item.get("last_draft_id") or ""),
        )
    return table


def _generic_title(command_path: str) -> str:
    return f"{command_path} response"


PRESENTERS: dict[str, Presenter] = {
    "account.list": account_list,
    "account.find": account_list,
    "tx.record": success_panel("Transaction recorded"),
    "recurring.reminders": recurring_reminders_table,
    "recurring.list": recurring_items_table,
    "recurring.draft_due": recurring_drafts_table,
    "capture": generic_payload_panel("Capture response"),
    "draft.confirm": generic_payload_panel("Draft confirm response"),
    "tx.list": generic_payload_panel(_generic_title("tx.list")),
    "tx.show": generic_payload_panel(_generic_title("tx.show")),
    "tx.reverse": generic_payload_panel(_generic_title("tx.reverse")),
    "balance.adjust": generic_payload_panel(_generic_title("balance.adjust")),
    "balance": generic_payload_panel(_generic_title("balance")),
    "expense.record": generic_payload_panel(_generic_title("expense.record")),
    "income.record": generic_payload_panel(_generic_title("income.record")),
    "recurring.create": generic_payload_panel(_generic_title("recurring.create")),
    "recurring.show": generic_payload_panel(_generic_title("recurring.show")),
    "recurring.update": generic_payload_panel(_generic_title("recurring.update")),
    "summary.accounts": generic_payload_panel(_generic_title("summary.accounts")),
    "summary.categories": generic_payload_panel(_generic_title("summary.categories")),
    "user.create": generic_payload_panel(_generic_title("user.create")),
    "user.list": generic_payload_panel(_generic_title("user.list")),
    "category.create": generic_payload_panel(_generic_title("category.create")),
    "category.list": generic_payload_panel(_generic_title("category.list")),
    "category.find": generic_payload_panel(_generic_title("category.find")),
    "category.show": generic_payload_panel(_generic_title("category.show")),
    "credit_card.list": generic_payload_panel(_generic_title("credit_card.list")),
    "credit_card.show": generic_payload_panel(_generic_title("credit_card.show")),
    "credit_card.update": generic_payload_panel(_generic_title("credit_card.update")),
    "account.create": generic_payload_panel(_generic_title("account.create")),
    "account.show": generic_payload_panel(_generic_title("account.show")),
    "account.update": generic_payload_panel(_generic_title("account.update")),
    "account.balance": generic_payload_panel(_generic_title("account.balance")),
    "account.adjust": generic_payload_panel(_generic_title("account.adjust")),
    "investment.event": generic_payload_panel(_generic_title("investment.event")),
    "investment.performance": generic_payload_panel(_generic_title("investment.performance")),
    "auth.dev_token": generic_payload_panel(_generic_title("auth.dev_token")),
    "auth.status": generic_payload_panel(_generic_title("auth.status")),
    "data.backup": generic_payload_panel(_generic_title("data.backup")),
    "auth.login": generic_payload_panel(_generic_title("auth.login")),
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
