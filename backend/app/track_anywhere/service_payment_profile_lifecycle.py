from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .domain_commands import CreatePaymentProfileCommand
from .errors import NotFound, ValidationError
from .payment_profiles import PaymentProfile


class PaymentProfileLifecycleUseCases:
    def create_payment_profile(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = CreatePaymentProfileCommand.model_validate(payload)
        instrument = self.storage.get_account(command.instrument_account_id)
        backing = self.storage.get_account(command.backing_account_id)

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
        self._commit_replay_or(replay, self._commit_payment_profile_change)
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
        return self.storage.list_payment_profiles(book_id=book_id, status=status)

    def payment_profile_status(self, token: str, profile_ref: str, *, book_id: str = DEFAULT_BOOK_ID) -> dict[str, Any]:
        profile = self.resolve_payment_profile(token, profile_ref, book_id=book_id)
        backing_balance = self.storage.account_balance(profile.backing_account_id).get(profile.backing_currency, Decimal("0"))
        instrument_clearing_balance = self.storage.account_balance(profile.instrument_account_id).get(
            profile.instrument_currency,
            Decimal("0"),
        )
        effective_instrument_balance = backing_balance / profile.settlement_rate
        return {
            "payment": profile.slug,
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "settlement_mode": profile.settlement_mode,
            "settlement_rate": profile.settlement_rate,
            "backing_balance": {
                "account_id": profile.backing_account_id,
                "amount": backing_balance,
                "currency": profile.backing_currency,
            },
            "effective_instrument_balance": {
                "account_id": profile.instrument_account_id,
                "amount": effective_instrument_balance,
                "currency": profile.instrument_currency,
            },
            "instrument_clearing_balance": {
                "account_id": profile.instrument_account_id,
                "amount": instrument_clearing_balance,
                "currency": profile.instrument_currency,
            },
        }

    def get_payment_profile(self, token: str, profile_id: str, *, include_inactive: bool = False) -> PaymentProfile:
        status = None if include_inactive else "active"
        profile = self.storage.get_payment_profile(profile_id, status=status)
        self.actor_for_book(token, profile.book_id, "ledger:read")
        return profile

    def resolve_payment_profile(self, token: str, profile_ref: str, *, book_id: str = DEFAULT_BOOK_ID) -> PaymentProfile:
        self.actor_for_book(token, book_id, "ledger:read")
        try:
            profile = self.storage.get_payment_profile(profile_ref)
        except NotFound:
            return self.storage.get_payment_profile_by_slug(book_id=book_id, slug=profile_ref)
        if profile.book_id != book_id:
            raise NotFound(f"payment profile not found in book: {book_id}/{profile_ref}")
        return profile

    def _resolve_payment_profile_reference(self, profile_ref: str) -> PaymentProfile:
        try:
            return self.storage.get_payment_profile(profile_ref)
        except NotFound:
            return self.storage.get_payment_profile_by_slug(book_id=DEFAULT_BOOK_ID, slug=profile_ref)
