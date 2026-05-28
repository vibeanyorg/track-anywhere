from __future__ import annotations

from .commands import CreateRecurringItemCommand
from .errors import ValidationError
from .recurring import RecurringItem


class RecurringItemValidationUseCases:
    def _validate_recurring_references(self, command: CreateRecurringItemCommand, book_id: str) -> None:
        if command.kind != "paid":
            return
        if command.currency is None or command.source_account_id is None or command.category_id is None:
            return
        source = self._get_account_from_storage(command.source_account_id)
        if source.book_id != book_id:
            raise ValidationError("recurring source account must belong to the recurring book")
        if source.currency != command.currency:
            raise ValidationError("recurring currency must match source account currency")
        category = self._get_category_from_storage(command.category_id)
        if category.book_id != book_id:
            raise ValidationError("recurring category must belong to the recurring book")
        if category.kind != "expense":
            raise ValidationError("paid recurring item requires an expense category")

    def _validate_item_references(self, item: RecurringItem) -> None:
        if item.kind != "paid":
            return
        if item.currency is None or item.source_account_id is None or item.category_id is None:
            return
        source = self._get_account_from_storage(item.source_account_id)
        if source.book_id != item.book_id:
            raise ValidationError("recurring source account must belong to the recurring book")
        if source.currency != item.currency:
            raise ValidationError("recurring currency must match source account currency")
        category = self._get_category_from_storage(item.category_id)
        if category.book_id != item.book_id:
            raise ValidationError("recurring category must belong to the recurring book")
        if category.kind != "expense":
            raise ValidationError("paid recurring item requires an expense category")
