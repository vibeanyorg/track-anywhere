from __future__ import annotations

from typing import Any

from .commands import RejectDraftCommand, SupersedeDraftCommand
from .errors import StaleVersion, ValidationError


class DraftLifecycleUseCases:
    def reject_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RejectDraftCommand.model_validate(payload)
        draft = self._stored_draft(command.draft_id)
        actor = self.actor_for_book(token, draft.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            if draft.version != command.expected_version:
                raise StaleVersion("draft version conflict")
            if draft.state in {"confirmed", "rejected", "superseded"}:
                raise ValidationError(f"draft cannot be rejected from state: {draft.state}")
            draft.state = "rejected"
            draft.version += 1
            self.drafts.drafts[draft.draft_id] = draft
            self.audit.record(
                operation="draft.reject",
                actor=actor,
                entity_ref=draft.draft_id,
                details={"reason": command.reason, "state": draft.state, "version": draft.version},
            )
            return draft

        rejected, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.reject",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_draft_change(rejected))
        return rejected, replay

    def supersede_draft(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "capture:draft")
        command = SupersedeDraftCommand.model_validate(payload)
        current = self._stored_draft(command.draft_id)
        self.books.require_access(current.book_id, actor, "capture:draft")
        request_hash = self._hash_command(command)

        def run():
            if current.version != command.expected_version:
                raise StaleVersion("draft version conflict")
            if current.state in {"confirmed", "rejected", "superseded"}:
                raise ValidationError(f"draft cannot be superseded from state: {current.state}")
            replacement = self._draft_from_capture_command(command.replacement, actor=actor)
            current.state = "superseded"
            current.version += 1
            self.drafts.drafts[current.draft_id] = current
            self.audit.record(
                operation="draft.supersede",
                actor=actor,
                entity_ref=command.draft_id,
                details={"replacement_draft_id": replacement.draft_id, "replacement_state": replacement.state},
            )
            return replacement

        replacement, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="draft.supersede",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(
            replay,
            lambda: self._commit_draft_change(current, replacement),
        )
        return replacement, replay
