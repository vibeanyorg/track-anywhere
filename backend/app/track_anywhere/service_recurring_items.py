from __future__ import annotations

from dataclasses import replace
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import CreateRecurringItemCommand, UpdateRecurringItemCommand
from .errors import ValidationError
from .recurring import Recurrence, RecurringItem, validate_recurring_item


class RecurringItemUseCases:
    def create_recurring_item(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[RecurringItem, bool]:
        command = CreateRecurringItemCommand.model_validate(payload)
        book_id = command.book_id or DEFAULT_BOOK_ID
        actor = self.actor_for_book(token, book_id, "recurring:write")
        self._validate_recurring_references(command, book_id)
        request_hash = self._hash_command_payload(command, {"book_id": book_id})

        def run():
            item = self.recurring.create(
                name=command.name,
                kind=command.kind,
                amount=command.amount,
                currency=command.currency,
                provider=command.provider,
                reference=command.reference,
                recurrence=Recurrence(**command.recurrence.model_dump()),
                reminder_days=command.reminder_days,
                anchor_date=command.anchor_date,
                book_id=book_id,
                source_account_id=command.source_account_id,
                category_id=command.category_id,
            )
            self.audit.record(
                operation="recurring.create",
                actor=actor,
                entity_ref=item.recurring_id,
                details=command.model_dump(mode="json", exclude_none=True),
            )
            return item

        item, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="recurring.create",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_recurring_change(item))
        return item, replay

    def update_recurring_item(
        self,
        token: str,
        recurring_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[RecurringItem, bool]:
        command = UpdateRecurringItemCommand.model_validate(payload)
        if not command.model_dump(exclude_none=True, exclude={"schema_version"}):
            raise ValidationError("at least one recurring item field is required")
        item = self.storage.get_recurring_item(recurring_id)
        actor = self.actor_for_book(token, item.book_id, "recurring:write")
        request_hash = self._hash_command_payload(command, {"recurring_id": recurring_id})

        def run():
            candidate = replace(
                item,
                recurrence=replace(item.recurrence),
                reminder_days=list(item.reminder_days),
            )
            self._apply_recurring_update(candidate, command)
            self._validate_item_references(candidate)
            validate_recurring_item(candidate)
            candidate.version = item.version + 1
            self.recurring.items[recurring_id] = candidate
            self.audit.record(
                operation="recurring.update",
                actor=actor,
                entity_ref=candidate.recurring_id,
                details={"recurring_id": recurring_id, **command.model_dump(mode="json", exclude_none=True)},
            )
            return candidate

        item, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="recurring.update",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_recurring_change(item))
        return item, replay

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

    def _validate_recurring_references(self, command: CreateRecurringItemCommand, book_id: str) -> None:
        if command.kind != "paid":
            return
        if command.currency is None or command.source_account_id is None or command.category_id is None:
            return
        source = self.storage.get_account(command.source_account_id)
        if source.book_id != book_id:
            raise ValidationError("recurring source account must belong to the recurring book")
        if source.currency != command.currency:
            raise ValidationError("recurring currency must match source account currency")
        category = self.storage.get_category(command.category_id)
        if category.book_id != book_id:
            raise ValidationError("recurring category must belong to the recurring book")
        if category.kind != "expense":
            raise ValidationError("paid recurring item requires an expense category")

    def _validate_item_references(self, item: RecurringItem) -> None:
        if item.kind != "paid":
            return
        if item.currency is None or item.source_account_id is None or item.category_id is None:
            return
        source = self.storage.get_account(item.source_account_id)
        if source.book_id != item.book_id:
            raise ValidationError("recurring source account must belong to the recurring book")
        if source.currency != item.currency:
            raise ValidationError("recurring currency must match source account currency")
        category = self.storage.get_category(item.category_id)
        if category.book_id != item.book_id:
            raise ValidationError("recurring category must belong to the recurring book")
        if category.kind != "expense":
            raise ValidationError("paid recurring item requires an expense category")

    @staticmethod
    def _apply_recurring_update(item: RecurringItem, command: UpdateRecurringItemCommand) -> None:
        updates = command.model_dump(exclude_none=True, exclude={"schema_version", "recurrence"})
        for field, value in updates.items():
            setattr(item, field, value)
        if command.recurrence is not None:
            item.recurrence = Recurrence(**command.recurrence.model_dump())
