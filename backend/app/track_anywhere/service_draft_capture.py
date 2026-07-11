from __future__ import annotations

from typing import Any

from .commands import CaptureDraftCommand
from .errors import ValidationError
from .ledger import Posting, credit_posting, debit_posting
from .security import Actor


class DraftCaptureUseCases:
    def capture_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Any, bool]:
        actor = self.actor_from_token(token, "capture:draft")
        command = CaptureDraftCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            draft = self._draft_from_capture_command(command, actor=actor)
            self.audit.record(
                operation="draft.capture",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"command": command.model_dump(mode="json"), "state": draft.state},
            )
            return draft

        draft, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.capture",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_draft_change(draft))
        return draft, replay

    def _draft_from_capture_command(self, command: CaptureDraftCommand, *, actor: Actor):
        proposed: list[Posting] = []
        missing: list[str] = []
        book_id = self.books.ensure_default().book_id
        for field in ("amount", "source_account_id", "expense_account_id"):
            if getattr(command, field) in (None, ""):
                missing.append(field)
        if not missing:
            amount = command.amount
            source_account_id = command.source_account_id
            expense_account_id = command.expense_account_id
            if amount is None or source_account_id is None or expense_account_id is None:
                raise ValidationError("complete draft command lost required posting fields")
            source = self._get_account_from_storage(source_account_id)
            expense = self._get_account_from_storage(expense_account_id)
            if source.book_id != expense.book_id:
                raise ValidationError("draft postings must belong to one book")
            if source.currency != command.currency or expense.currency != command.currency:
                raise ValidationError("draft posting currency must match all referenced account currencies")
            book_id = source.book_id
            proposed.extend(
                [
                    credit_posting(source_account_id, amount, command.currency),
                    debit_posting(expense_account_id, amount, command.currency),
                ]
            )
        return self.drafts.create(
            memo=command.memo,
            proposed_postings=proposed,
            missing_fields=missing,
            source=actor.actor_type,
            confidence=command.confidence,
            book_id=book_id,
        )
