from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .domain_commands import CreateCounterpartyCommand
from .errors import NotFound, ValidationError


class CounterpartyUseCases:
    def ensure_counterparty(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = CreateCounterpartyCommand.model_validate(payload)
        book_id = command.book_id or DEFAULT_BOOK_ID
        actor = self.actor_for_book(token, book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            counterparty = self.counterparties.ensure(
                book_id=book_id,
                name=command.name,
                kind=command.kind,
                slug=command.slug,
            )
            self.audit.record(
                operation="counterparty.ensure",
                actor=actor,
                entity_ref=counterparty.counterparty_id,
                details=command.model_dump(mode="json"),
            )
            return counterparty

        counterparty, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="counterparty.ensure",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._commit_idempotency()
        else:
            self._commit_catalog_change()
        return counterparty, replay

    def list_counterparties(
        self,
        token: str,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        kind: str | None = None,
        status: str | None = "active",
        name: str | None = None,
    ):
        self.actor_for_book(token, book_id, "ledger:read")
        if status is not None and status not in {"active", "hidden", "archived"}:
            raise ValidationError("status must be active, hidden, or archived")
        return self.storage.list_counterparties(book_id=book_id, kind=kind, status=status, name=name)

    def get_counterparty(self, token: str, counterparty_ref: str, *, book_id: str = DEFAULT_BOOK_ID):
        self.actor_for_book(token, book_id, "ledger:read")
        return self._resolve_counterparty_reference(counterparty_ref, book_id=book_id)

    def _resolve_counterparty_reference(self, counterparty_ref: str, *, book_id: str = DEFAULT_BOOK_ID):
        try:
            counterparty = self.storage.get_counterparty(counterparty_ref)
        except NotFound:
            pass
        else:
            if counterparty.book_id != book_id:
                raise NotFound(f"counterparty not found in book: {book_id}/{counterparty_ref}")
            return counterparty
        try:
            return self.storage.get_counterparty_by_slug(book_id=book_id, slug=counterparty_ref)
        except NotFound:
            return self.storage.get_counterparty_by_name(book_id=book_id, name=counterparty_ref)

    def _resolve_counterparty_for_write(self, counterparty_ref: str | None, *, book_id: str = DEFAULT_BOOK_ID):
        if counterparty_ref is None:
            return None
        try:
            return self.counterparties.resolve(book_id=book_id, ref=counterparty_ref)
        except NotFound:
            pass
        try:
            counterparty = self._resolve_counterparty_reference(counterparty_ref, book_id=book_id)
        except NotFound as exc:
            raise NotFound(f"counterparty not found: {counterparty_ref}") from exc
        self.counterparties.counterparties[counterparty.counterparty_id] = counterparty
        return counterparty
