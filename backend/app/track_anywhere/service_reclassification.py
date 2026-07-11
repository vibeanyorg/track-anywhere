from __future__ import annotations

from typing import Any

from .commands import ReclassifyTransactionCommand
from .errors import NotFound, ValidationError
from .ledger import Transaction


class ReclassificationUseCases:
    def reclassify_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = ReclassifyTransactionCommand.model_validate(payload)
        transaction = self._get_transaction_from_storage(command.transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {command.transaction_id}")
        category = self.categories.get(command.category_id)
        if category.book_id != transaction.book_id:
            raise ValidationError("transaction category must belong to the same book")
        actor = self.actor_for_book(token, transaction.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)
        changed_line_id: str | None = None

        def run():
            nonlocal changed_line_id
            line = self._line_for_reclassification(transaction, line_id=command.line_id, category_kind=category.kind)
            if line is None:
                before_count = len(transaction.lines)
                self._add_category_line_for_transaction(transaction, category)
                if len(transaction.lines) == before_count:
                    raise ValidationError("transaction has no amount matching the target category kind")
                line = transaction.lines[-1]
                before = {"category_id": None, "category_version_id": None, "category_path_snapshot": None}
            else:
                before = {
                    "category_id": line.category_id,
                    "category_version_id": line.category_version_id,
                    "category_path_snapshot": line.category_path_snapshot,
                }
                line.category_id = category.category_id
                line.category_version_id = self.categories.active_version(category.category_id).category_version_id
                line.category_path_snapshot = self.categories.path_snapshot(category.category_id)
            after = {
                "category_id": line.category_id,
                "category_version_id": line.category_version_id,
                "category_path_snapshot": line.category_path_snapshot,
                "line_id": line.line_id,
                "transaction_id": transaction.transaction_id,
                "memo": command.memo,
            }
            if before != {key: after.get(key) for key in ("category_id", "category_version_id", "category_path_snapshot")}:
                line.version += 1
            changed_line_id = line.line_id
            self.categories._record_event(
                "reclassify",
                transaction.book_id,
                before.get("category_id"),
                target_id=category.category_id,
                affected_line_count=1,
                before={**before, "line_id": line.line_id, "transaction_id": transaction.transaction_id},
                after=after,
                actor_id=actor.actor_id,
            )
            self.audit.record(
                operation="ledger.transaction.reclassify",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=after,
            )
            return transaction

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.transaction.reclassify",
            request_hash=request_hash,
            fn=run,
        )

        def commit() -> None:
            if changed_line_id is None:
                raise ValidationError("reclassification did not select a transaction line")
            self._commit_reclassification_change(result, changed_line_id)

        self._commit_replay_or(replay, commit)
        return result, replay

    def _line_for_reclassification(self, transaction: Transaction, *, line_id: str | None, category_kind: str):
        if line_id is not None:
            for line in transaction.lines:
                if line.line_id == line_id:
                    if line.line_type != category_kind:
                        raise ValidationError("target category kind must match the selected transaction line")
                    return line
            raise NotFound(f"transaction line not found: {line_id}")
        candidates = [line for line in transaction.lines if line.line_type == category_kind]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise ValidationError("transaction has multiple matching lines; pass --line-id")
        return candidates[0]
