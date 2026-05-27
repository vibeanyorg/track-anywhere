from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import CheckRecurringCommand
from .recurring import RecurringItem, due_reminders


class RecurringReminderUseCases:
    def check_recurring_reminders(
        self,
        token: str,
        *,
        as_of: str | None = None,
        window_days: int = 0,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> dict[str, Any]:
        self.actor_for_book(token, book_id, "recurring:read")
        payload = {"window_days": window_days}
        if as_of is not None:
            payload["as_of"] = as_of
        command = CheckRecurringCommand.model_validate(payload)
        reminders = []
        for item in self._list_recurring_items_from_storage(status="active", book_id=book_id):
            for reminder in due_reminders(item, command.as_of, command.window_days):
                reminders.append(self._recurring_reminder_payload(item, reminder))
        reminders.sort(key=lambda item: (item["reminder_date"], item["renewal_date"], item["name"]))
        return {"as_of": command.as_of.isoformat(), "window_days": command.window_days, "reminders": reminders}

    @staticmethod
    def _recurring_reminder_payload(item: RecurringItem, reminder: dict[str, Any]) -> dict[str, Any]:
        return {
            "recurring_id": item.recurring_id,
            "name": item.name,
            "kind": item.kind,
            "provider": item.provider,
            "reference": item.reference,
            "amount": str(item.amount) if item.amount is not None else None,
            "currency": item.currency,
            "renewal_date": reminder["renewal_date"].isoformat(),
            "reminder_date": reminder["reminder_date"].isoformat(),
            "lead_days": reminder["lead_days"],
        }
