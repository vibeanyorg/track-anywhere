from __future__ import annotations

from typing import Any

from .commands import CaptureDraftCommand, ConfirmDraftCommand, RejectDraftCommand, SupersedeDraftCommand
from .errors import NotFound, StaleVersion, ValidationError
from .ledger import Posting, Transaction
from .security import Actor


class DraftUseCases:
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

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.capture",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def confirm_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Transaction, bool]:
        command = ConfirmDraftCommand.model_validate(payload)
        draft = self.drafts.get(command.draft_id)
        if draft is None:
            raise NotFound(f"draft not found: {command.draft_id}")
        actor = self.actor_for_book(token, draft.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            if draft.version != command.expected_version:
                raise StaleVersion("draft version conflict")
            if draft.state != "ready_to_confirm":
                raise ValidationError("draft is not ready to confirm")
            transaction = self.ledger.create_transaction(
                draft.memo,
                draft.proposed_postings,
                category_id=draft.category_id,
                book_id=draft.book_id,
            )
            if draft.category_id is not None:
                self._add_category_line_for_transaction(transaction, self.categories.get(draft.category_id))
            draft.state = "confirmed"
            draft.version += 1
            self.audit.record(
                operation="draft.confirm",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"transaction_id": transaction.transaction_id},
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.confirm",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def reject_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RejectDraftCommand.model_validate(payload)
        draft = self.drafts.get(command.draft_id)
        if draft is None:
            raise NotFound(f"draft not found: {command.draft_id}")
        actor = self.actor_for_book(token, draft.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            rejected = self.drafts.reject(command.draft_id, command.expected_version)
            self.audit.record(
                operation="draft.reject",
                actor=actor,
                entity_ref=rejected.draft_id,
                details={"reason": command.reason, "state": rejected.state, "version": rejected.version},
            )
            return rejected

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.reject",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def supersede_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "capture:draft")
        command = SupersedeDraftCommand.model_validate(payload)
        current = self.drafts.get(command.draft_id)
        if current is None:
            raise NotFound(f"draft not found: {command.draft_id}")
        self.books.require_access(current.book_id, actor, "capture:draft")
        request_hash = self._hash_command(command)

        def run():
            replacement = self._draft_from_capture_command(command.replacement, actor=actor)
            replacement = self.drafts.supersede(command.draft_id, command.expected_version, replacement)
            self.audit.record(
                operation="draft.supersede",
                actor=actor,
                entity_ref=command.draft_id,
                details={"replacement_draft_id": replacement.draft_id, "replacement_state": replacement.state},
            )
            return replacement

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.supersede",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

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
            source = self.ledger.get_account(source_account_id)
            expense = self.ledger.get_account(expense_account_id)
            if source.book_id != expense.book_id:
                raise ValidationError("draft postings must belong to one book")
            book_id = source.book_id
            proposed.extend(
                [
                    Posting(source_account_id, -amount, command.currency),
                    Posting(expense_account_id, amount, command.currency),
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
