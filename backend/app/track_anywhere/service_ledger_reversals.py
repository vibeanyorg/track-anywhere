from __future__ import annotations

from typing import Any

from .commands import ReverseTransactionCommand
from .errors import NotFound, ValidationError
from .ledger import Posting
from .transaction_builder import build_transaction


class LedgerReversalUseCases:
    def reverse_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = ReverseTransactionCommand.model_validate(payload)
        transaction = self._get_transaction_from_storage(command.transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {command.transaction_id}")
        if transaction.reversed_by is not None:
            raise ValidationError("transaction is already reversed")
        if transaction.reverses_transaction_id is not None:
            raise ValidationError("reversal transactions cannot be reversed directly")
        actor = self.actor_for_book(token, transaction.book_id, "ledger:reverse")
        request_hash = self._hash_command(command)

        def run():
            reversal = build_transaction(
                memo=command.memo,
                purpose="reversal",
                postings=[
                    Posting(account_id=posting.account_id, amount=-posting.amount, currency=posting.currency)
                    for posting in transaction.postings
                ],
                book_id=transaction.book_id,
                reverses_transaction_id=transaction.transaction_id,
                accounts=[self._transaction_account(posting.account_id) for posting in transaction.postings],
                scale_lookup=self.assets.scale_for,
            )
            transaction.reversed_by = reversal.transaction_id
            self.audit.record(
                operation="ledger.reverse",
                actor=actor,
                entity_ref=command.transaction_id,
                details={"reversal_transaction_id": reversal.transaction_id},
            )
            return reversal

        reversal, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.reverse",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction, reversal))
        return reversal, replay
