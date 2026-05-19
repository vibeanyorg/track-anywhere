from __future__ import annotations

from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

Presenter = Callable[[Any], Any]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def object_summary(title: str, fields: list[tuple[str, Any]]) -> Table:
    table = Table(title=title, show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    for label, value in fields:
        table.add_row(label, _stringify(value))
    return table


def empty_panel(title: str, message: str) -> Panel:
    return Panel(message, title=title)
