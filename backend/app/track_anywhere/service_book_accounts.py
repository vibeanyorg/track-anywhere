from __future__ import annotations

from typing import Any

from .commands import CreateAccountCommand


class BookAccountUseCases:
    def list_book_accounts(self, token: str, book_id: str) -> list[Any]:
        return self.list_accounts(token, book_id=book_id)

    def create_book_account(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "account:write")
        CreateAccountCommand.model_validate({**payload, "book_id": book_id})
        return self.create_account(token, {**payload, "book_id": book_id}, idempotency_key=idempotency_key)
