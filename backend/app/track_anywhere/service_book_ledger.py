from __future__ import annotations

from typing import Any

from .commands import RecordTransactionCommand, ReverseTransactionCommand
from .errors import ValidationError


class BookLedgerUseCases:
    def list_book_transactions(self, token: str, book_id: str, *, limit: int = 20) -> list[Any]:
        self.actor_for_book(token, book_id, "ledger:read")
        return self._list_transactions_from_storage(book_id=book_id, limit=limit)

    def record_book_transaction(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "ledger:confirm")
        command = RecordTransactionCommand.model_validate(payload)
        for account_id in (command.from_account_id, command.to_account_id):
            if self._get_account_from_storage(account_id).book_id != book_id:
                raise ValidationError("book transaction accounts must belong to the route book")
        return self.record_transaction(token, payload, idempotency_key=idempotency_key)

    def reverse_book_transaction(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "ledger:reverse")
        command = ReverseTransactionCommand.model_validate(payload)
        transaction = self.get_transaction(token, command.transaction_id)
        if transaction.book_id != book_id:
            raise ValidationError("transaction does not belong to book")
        return self.reverse_transaction(token, payload, idempotency_key=idempotency_key)
