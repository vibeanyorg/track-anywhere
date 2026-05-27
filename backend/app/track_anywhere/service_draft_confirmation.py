from __future__ import annotations

from typing import Any

from .commands import ConfirmDraftCommand
from .errors import StaleVersion, ValidationError
from .ledger import Transaction
from .transaction_builder import build_transaction


class DraftConfirmationUseCases:
    def confirm_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Transaction, bool]:
        command = ConfirmDraftCommand.model_validate(payload)
        draft = self._stored_draft(command.draft_id)
        actor = self.actor_for_book(token, draft.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            if draft.version != command.expected_version:
                raise StaleVersion("draft version conflict")
            if draft.state != "ready_to_confirm":
                raise ValidationError("draft is not ready to confirm")
            transaction = build_transaction(
                memo=draft.memo,
                purpose="draft_confirmed",
                postings=draft.proposed_postings,
                book_id=draft.book_id,
                accounts=[self._transaction_account(posting.account_id) for posting in draft.proposed_postings],
                scale_lookup=self.assets.scale_for,
            )
            if draft.category_id is not None:
                self._add_category_line_for_transaction(transaction, self.storage.get_category(draft.category_id))
            draft.state = "confirmed"
            draft.version += 1
            self.drafts.drafts[draft.draft_id] = draft
            self.audit.record(
                operation="draft.confirm",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"transaction_id": transaction.transaction_id},
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.confirm",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._commit_idempotency()
        else:
            self._commit_draft_change(draft, transactions=(transaction,))
        return transaction, replay
