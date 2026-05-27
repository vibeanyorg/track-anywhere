from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .domain_commands import CreatePaymentInstrumentCommand
from .errors import NotFound, ValidationError


class PaymentInstrumentUseCases:
    def create_payment_instrument(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = CreatePaymentInstrumentCommand.model_validate(payload)
        account = self.storage.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "credit-card:write")
        self._validate_payment_instrument_account(command.kind, account)
        request_hash = self._hash_command(command)

        def run():
            if self.payment_instruments.get_optional_by_slug(
                book_id=account.book_id,
                slug=command.slug,
                status=None,
            ) is not None:
                raise ValidationError("payment instrument slug already exists")
            instrument = self.payment_instruments.create(
                book_id=account.book_id,
                slug=command.slug,
                display_name=command.display_name,
                kind=command.kind,
                account_id=command.account_id,
                last4=command.last4,
            )
            self.audit.record(
                operation="payment_instrument.create",
                actor=actor,
                entity_ref=instrument.instrument_id,
                details=command.model_dump(mode="json"),
            )
            return instrument

        instrument, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="payment_instrument.create",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._commit_idempotency()
        else:
            self._commit_catalog_change()
        return instrument, replay

    def list_payment_instruments(
        self,
        token: str,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        account_id: str | None = None,
        status: str | None = "active",
    ):
        if account_id is not None:
            account = self.storage.get_account(account_id)
            book_id = account.book_id
        self.actor_for_book(token, book_id, "credit-card:read")
        if status is not None and status not in {"active", "hidden", "archived"}:
            raise ValidationError("status must be active, hidden, or archived")
        return self.storage.list_payment_instruments(book_id=book_id, account_id=account_id, status=status)

    def get_payment_instrument(self, token: str, instrument_id: str, *, include_inactive: bool = False):
        status = None if include_inactive else "active"
        instrument = self.storage.get_payment_instrument(instrument_id, status=status)
        self.actor_for_book(token, instrument.book_id, "credit-card:read")
        return instrument

    def resolve_payment_instrument(self, token: str, instrument_ref: str, *, book_id: str = DEFAULT_BOOK_ID):
        self.actor_for_book(token, book_id, "credit-card:read")
        try:
            instrument = self.storage.get_payment_instrument(instrument_ref)
        except NotFound:
            return self.storage.get_payment_instrument_by_slug(book_id=book_id, slug=instrument_ref)
        if instrument.book_id != book_id:
            raise NotFound(f"payment instrument not found in book: {book_id}/{instrument_ref}")
        return instrument

    @staticmethod
    def _validate_payment_instrument_account(kind: str, account) -> None:
        if kind == "credit_card" and (account.type != "liability" or account.subtype != "credit_card"):
            raise ValidationError("credit card instrument requires a liability account with subtype credit_card")
