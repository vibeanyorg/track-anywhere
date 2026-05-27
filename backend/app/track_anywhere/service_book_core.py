from __future__ import annotations

from typing import Any

from .books import LedgerBook
from .domain_commands import CreateBookCommand


class BookCoreUseCases:
    def list_books(self, token: str) -> list[LedgerBook]:
        actor = self.actor_from_token(token, "book:read")
        if actor.actor_id == "owner":
            return self.books.list()
        member_book_ids = {book_id for book_id, user_id in self.books.members if user_id == actor.actor_id}
        return [book for book in self.books.list() if book.book_id in member_book_ids]

    def get_book(self, token: str, book_id: str) -> LedgerBook:
        actor = self.actor_for_book(token, book_id, "book:read")
        return self.books.require_access(book_id, actor, "book:read")

    def create_book(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[LedgerBook, bool]:
        actor = self.actor_from_token(token, "book:write")
        command = CreateBookCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            book = self.books.create(
                name=command.name,
                kind=command.kind,
                base_currency=command.base_currency,
                timezone=command.timezone,
                template_key=command.template_key,
                created_by=actor.actor_id,
            )
            self.audit.record(operation="book.create", actor=actor, entity_ref=book.book_id, details=command.model_dump(mode="json"))
            return book

        book, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="book.create", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, self._commit_book_change)
        return book, replay
