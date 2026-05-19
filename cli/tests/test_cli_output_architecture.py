from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from track_anywhere_cli.renderers import _render_human


def test_no_dead_recurring_human_helpers_in_renderer():
    text = Path("cli/track_anywhere_cli/renderers.py").read_text()

    assert "_recurring_reminders_table" not in text
    assert "_recurring_items_table" not in text
    assert "_recurring_drafts_table" not in text


def test_no_raw_human_dict_fallback_in_renderer():
    text = Path("cli/track_anywhere_cli/renderers.py").read_text()

    assert "return data" not in text


def test_render_human_recurring_reminders_still_renders_table():
    renderable = _render_human(
        {"reminders": [{"name": "ChatGPT", "provider": "OpenAI", "renewal_date": "2026-06-15", "reminder_date": "2026-06-12", "lead_days": 3, "amount": "20", "currency": "USD"}]},
        "recurring.reminders",
    )
    assert isinstance(renderable, Table)
    assert renderable.title == "Recurring reminders"
    assert [col.header for col in renderable.columns] == ["Name", "Provider", "Renewal", "Reminder", "Lead", "Amount"]
    console = Console(force_terminal=False, width=120, record=True)
    console.print(renderable)
    captured = console.export_text()
    assert "ChatGPT" in captured
    assert "Renewal" in captured


def test_render_human_unknown_command_returns_panel():
    renderable = _render_human({"foo": "bar"}, "unknown.command")

    assert isinstance(renderable, Panel)
    assert renderable.title == "unknown.command"
