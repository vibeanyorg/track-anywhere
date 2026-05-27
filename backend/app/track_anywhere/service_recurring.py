from __future__ import annotations

from dataclasses import replace
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import (
    CheckRecurringCommand,
    CreateRecurringItemCommand,
    GenerateRecurringDraftsCommand,
    UpdateRecurringItemCommand,
)
from .errors import PolicyDenied, ValidationError
from .ledger import Posting
from .recurring import Recurrence, RecurringItem, due_reminders, last_renewal_date, validate_recurring_item


class RecurringUseCases:
    def create_recurring_item(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[RecurringItem, bool]:
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
        if replay:
            self._commit_idempotency()
        else:
            self._commit_recurring_change(item)
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
        if replay:
            self._commit_idempotency()
        else:
            self._commit_recurring_change(item)
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
        for item in self.storage.list_recurring_items(status="active", book_id=book_id):
            for reminder in due_reminders(item, command.as_of, command.window_days):
                reminders.append(self._recurring_reminder_payload(item, reminder))
        reminders.sort(key=lambda item: (item["reminder_date"], item["renewal_date"], item["name"]))
        return {"as_of": command.as_of.isoformat(), "window_days": command.window_days, "reminders": reminders}

    def generate_recurring_drafts(
        self,
        token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> tuple[dict[str, Any], bool]:
        actor = self.actor_for_book(token, book_id, "recurring:write")
        if "capture:draft" not in actor.scopes:
            raise PolicyDenied("credential lacks required scope: capture:draft")
        command = GenerateRecurringDraftsCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id})
        created_accounts = []

        def run():
            result = {"as_of": command.as_of.isoformat(), "created": [], "skipped": []}
            for item in self.storage.list_recurring_items(status="active", book_id=book_id):
                renewal_date = last_renewal_date(item, command.as_of)
                if renewal_date is None:
                    result["skipped"].append(self._recurring_skip(item, "not_due", None))
                    continue
                if item.kind != "paid":
                    result["skipped"].append(self._recurring_skip(item, "not_paid", renewal_date))
                    continue
                if item.last_draft_renewal_date == renewal_date:
                    result["skipped"].append(self._recurring_skip(item, "already_generated", renewal_date))
                    continue
                created = self._create_recurring_draft(item, renewal_date, created_accounts=created_accounts)
                result["created"].append(created)
            self.audit.record(
                operation="recurring.draft.generate",
                actor=actor,
                entity_ref=None,
                details=result,
            )
            return result

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="recurring.draft.generate",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._commit_idempotency()
        else:
            draft_ids = [item["draft_id"] for item in result["created"]]
            drafts = [self.drafts.drafts[draft_id] for draft_id in draft_ids]
            recurring_ids = [item["recurring_id"] for item in result["created"]]
            recurring_items = [self.recurring.items[recurring_id] for recurring_id in recurring_ids]
            self._commit_recurring_change(*recurring_items, drafts=drafts, accounts=created_accounts)
        return result, replay

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

    def _create_recurring_draft(self, item: RecurringItem, renewal_date, *, created_accounts):
        if item.amount is None or item.currency is None or item.source_account_id is None or item.category_id is None:
            raise ValidationError("paid recurring item lost required draft fields")
        expense_account = self._system_category_account("expense", item.currency, book_id=item.book_id, created_accounts=created_accounts)
        expense_account_id = expense_account.account_id
        draft = self.drafts.create(
            memo=f"Recurring renewal: {item.name} ({renewal_date.isoformat()})",
            proposed_postings=[
                Posting(item.source_account_id, -item.amount, item.currency),
                Posting(expense_account_id, item.amount, item.currency),
            ],
            missing_fields=[],
            source="recurring",
            confidence=1.0,
            category_id=item.category_id,
            book_id=item.book_id,
            metadata={
                "recurring_id": item.recurring_id,
                "renewal_date": renewal_date.isoformat(),
                "provider": item.provider,
                "reference": item.reference,
            },
        )
        item.last_draft_renewal_date = renewal_date
        item.last_draft_id = draft.draft_id
        item.version += 1
        self.recurring.items[item.recurring_id] = item
        return {
            "recurring_id": item.recurring_id,
            "draft_id": draft.draft_id,
            "renewal_date": renewal_date.isoformat(),
            "status": "created",
        }

    def _recurring_skip(self, item: RecurringItem, reason: str, renewal_date) -> dict[str, Any]:
        return {
            "recurring_id": item.recurring_id,
            "name": item.name,
            "reason": reason,
            "renewal_date": renewal_date.isoformat() if renewal_date else None,
            "last_draft_id": item.last_draft_id,
        }

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
