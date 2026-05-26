from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .domain_commands import CreatePaymentProfileCommand
from .errors import NotFound, ValidationError
from .payment_profiles import PaymentProfile


class PaymentProfileUseCases:
    def create_payment_profile(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = CreatePaymentProfileCommand.model_validate(payload)
        instrument = self.ledger.get_account(command.instrument_account_id)
        backing = self.ledger.get_account(command.backing_account_id)

        if instrument.book_id != backing.book_id:
            raise ValidationError("payment profile accounts must belong to one book")
        if instrument.currency == backing.currency:
            raise ValidationError("payment profile instrument and backing currencies must differ")
        actor = self.actor_for_book(token, instrument.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            if self.payment_profiles.get_optional_by_slug(
                book_id=instrument.book_id,
                slug=command.slug,
                status=None,
            ) is not None:
                raise ValidationError("payment profile slug already exists")
            profile = self.payment_profiles.create(
                book_id=instrument.book_id,
                slug=command.slug,
                display_name=command.display_name,
                kind=command.kind,
                instrument_account_id=command.instrument_account_id,
                instrument_currency=instrument.currency,
                backing_account_id=command.backing_account_id,
                backing_currency=backing.currency,
                settlement_mode=command.settlement_mode,
                settlement_rate=command.settlement_rate,
            )
            self.audit.record(
                operation="payment_profile.create",
                actor=actor,
                entity_ref=profile.profile_id,
                details=command.model_dump(mode="json"),
            )
            return profile

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="payment_profile.create",
            request_hash=request_hash,
            fn=run,
        )
        profile, replay = result
        if replay:
            self._persist_idempotency()
        else:
            self._persist()
        return profile, replay

    def list_payment_profiles(
        self,
        token: str,
        *,
        book_id: str = DEFAULT_BOOK_ID,
        status: str | None = "active",
    ) -> list[PaymentProfile]:
        self.actor_for_book(token, book_id, "ledger:read")
        if status is not None and status not in {"active", "hidden", "archived"}:
            raise ValidationError("status must be active, hidden, or archived")
        return self.payment_profiles.list(book_id=book_id, status=status)

    def get_payment_profile(self, token: str, profile_id: str, *, include_inactive: bool = False) -> PaymentProfile:
        status = None if include_inactive else "active"
        profile = self.payment_profiles.get(profile_id, status=status)
        self.actor_for_book(token, profile.book_id, "ledger:read")
        return profile

    def resolve_payment_profile(self, token: str, profile_ref: str, *, book_id: str = DEFAULT_BOOK_ID) -> PaymentProfile:
        self.actor_for_book(token, book_id, "ledger:read")
        try:
            profile = self.payment_profiles.get(profile_ref)
        except NotFound:
            return self.payment_profiles.get_by_slug(book_id=book_id, slug=profile_ref)
        if profile.book_id != book_id:
            raise NotFound(f"payment profile not found in book: {book_id}/{profile_ref}")
        return profile
