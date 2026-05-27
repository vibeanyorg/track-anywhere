from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import GenerateRecurringDraftsCommand
from .errors import PolicyDenied, ValidationError
from .ledger import Posting
from .recurring import RecurringItem, last_renewal_date


class RecurringDraftUseCases:
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
            for item in self._list_recurring_items_from_storage(status="active", book_id=book_id):
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

    def _create_recurring_draft(self, item: RecurringItem, renewal_date, *, created_accounts):
        if item.amount is None or item.currency is None or item.source_account_id is None or item.category_id is None:
            raise ValidationError("paid recurring item lost required draft fields")
        expense_account = self._system_category_account(
            "expense",
            item.currency,
            book_id=item.book_id,
            created_accounts=created_accounts,
        )
        draft = self.drafts.create(
            memo=f"Recurring renewal: {item.name} ({renewal_date.isoformat()})",
            proposed_postings=[
                Posting(item.source_account_id, -item.amount, item.currency),
                Posting(expense_account.account_id, item.amount, item.currency),
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
