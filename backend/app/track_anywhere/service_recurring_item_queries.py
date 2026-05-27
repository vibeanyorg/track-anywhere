from __future__ import annotations

from .books import DEFAULT_BOOK_ID
from .errors import ValidationError
from .recurring import RecurringItem


class RecurringItemQueryUseCases:
    def list_recurring_items(
        self,
        token: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> list[RecurringItem]:
        self.actor_for_book(token, book_id, "recurring:read")
        if status is not None and status not in {"active", "paused", "cancelled"}:
            raise ValidationError("status must be active, paused, or cancelled")
        if kind is not None and kind not in {"paid", "reminder_only"}:
            raise ValidationError("kind must be paid or reminder_only")
        return self.storage.list_recurring_items(status=status, kind=kind, book_id=book_id)

    def get_recurring_item(self, token: str, recurring_id: str) -> RecurringItem:
        item = self.storage.get_recurring_item(recurring_id)
        self.actor_for_book(token, item.book_id, "recurring:read")
        return item
