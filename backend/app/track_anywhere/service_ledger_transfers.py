from __future__ import annotations

from typing import Any

from .commands import RecordTransactionCommand
from .errors import ValidationError
from .ledger import Posting
from .transaction_builder import build_transaction


class LedgerTransferUseCases:
    def record_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RecordTransactionCommand.model_validate(payload)
        from_account = self.storage.get_account(command.from_account_id)
        to_account = self.storage.get_account(command.to_account_id)
        if from_account.book_id != to_account.book_id:
            raise ValidationError("transaction accounts must belong to one book")
        if from_account.currency != command.currency or to_account.currency != command.currency:
            raise ValidationError("transaction currency must match both account currencies")
        self.assets.validate_amount(command.currency, command.amount)
        actor = self.actor_for_book(token, from_account.book_id, "ledger:confirm")
        category = None
        if command.category_id is not None:
            category = self.storage.get_category(command.category_id)
            if category.book_id != from_account.book_id:
                raise ValidationError("transaction category must belong to the same book")
            self._validate_transaction_category(
                category,
                from_account_id=command.from_account_id,
                to_account_id=command.to_account_id,
            )
        if command.counterparty is not None and category is None:
            raise ValidationError("transaction counterparty requires a category")
        request_hash = self._hash_command(command)

        def run():
            counterparty = self._resolve_counterparty_for_write(command.counterparty, book_id=from_account.book_id)
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.from_account_id, -command.amount, command.currency),
                    Posting(command.to_account_id, command.amount, command.currency),
                ],
                accounts=[from_account, to_account],
                scale_lookup=self.assets.scale_for,
            )
            if category is not None:
                self._add_category_line_for_transaction(
                    transaction,
                    category,
                    accounts=(from_account, to_account),
                    counterparty_id=counterparty.counterparty_id if counterparty else None,
                )
            self.audit.record(
                operation="ledger.transaction.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=command.model_dump(mode="json"),
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.transaction.record",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction))
        return transaction, replay
