from __future__ import annotations

from dataclasses import replace
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import CreateRecurringItemCommand, UpdateRecurringItemCommand
from .errors import ValidationError
from .recurring import Recurrence, RecurringItem, validate_recurring_item


class RecurringItemCommandUseCases:
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

    @staticmethod
    def _apply_recurring_update(item: RecurringItem, command: UpdateRecurringItemCommand) -> None:
        updates = command.model_dump(exclude_none=True, exclude={"schema_version", "recurrence"})
        for field, value in updates.items():
            setattr(item, field, value)
        if command.recurrence is not None:
            item.recurrence = Recurrence(**command.recurrence.model_dump())
