from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from .output import CliOutcome, outcome_to_json_document
from .presenters import presenter_for


def emit_outcome(outcome: CliOutcome, *, json_mode: bool, no_color: bool) -> None:
    if json_mode:
        print(outcome_to_json_document(outcome))
        return
    console = Console(no_color=no_color)
    renderable = _render_human(outcome.data, outcome.command_path)
    console.print(renderable)


def emit_result(
    data: Any,
    *,
    json_mode: bool,
    no_color: bool,
    command_path: str = "",
    status: int = 200,
) -> None:
    from .runtime import build_outcome

    emit_outcome(
        build_outcome(command_path, status, data),
        json_mode=json_mode,
        no_color=no_color,
    )


def _render_human(data: Any, command_path: str):
    if isinstance(data, str):
        renderable = data
        return renderable
    try:
        return presenter_for(command_path)(data)
    except KeyError:
        return Panel("No human presenter registered.", title=command_path or "Command")
