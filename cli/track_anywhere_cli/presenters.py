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


PRESENTERS: dict[str, Presenter] = {
    "account.list": account_list,
    "account.find": account_list,
    "tx.record": success_panel("Transaction recorded"),
}


def presenter_for(command_path: str) -> Presenter:
    return PRESENTERS[command_path]
