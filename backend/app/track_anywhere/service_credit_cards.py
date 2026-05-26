from __future__ import annotations

from decimal import Decimal
from typing import Any

from .commands import UpdateCreditCardProfileCommand
from .errors import ValidationError
from .ledger import Account


class CreditCardUseCases:
    def list_credit_cards(self, token: str) -> list[dict[str, Any]]:
        self.actor_from_token(token, "credit-card:read")
        accounts = [
            account
            for account in self.ledger.accounts.values()
            if account.type == "liability" and account.subtype == "credit_card"
        ]
        return [self._credit_card_overview(account.account_id) for account in sorted(accounts, key=lambda item: item.name)]

    def get_credit_card(self, token: str, account_id: str) -> dict[str, Any]:
        self.actor_from_token(token, "credit-card:read")
        self._require_credit_card_account(account_id)
        return self._credit_card_overview(account_id)

    def update_credit_card_profile(
        self,
        token: str,
        account_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ):
        actor = self.actor_from_token(token, "credit-card:write")
        self._require_credit_card_account(account_id)
        command = UpdateCreditCardProfileCommand.model_validate(payload)
        update_fields = command.model_fields_set - {"schema_version"}
        if not update_fields:
            raise ValidationError("at least one credit card profile field is required")
        allowed_fields = {"credit_limit", "available_credit", "statement_day", "due_day", "annual_fee"}
        update_fields &= allowed_fields
        request_hash = self._hash_command_payload(command, {"account_id": account_id, "update_fields": sorted(update_fields)})

        def run():
            profile = self.credit_cards.upsert(
                account_id,
                credit_limit=command.credit_limit,
                available_credit=command.available_credit,
                statement_day=command.statement_day,
                due_day=command.due_day,
                annual_fee=command.annual_fee,
                update_fields=update_fields,
            )
            self.audit.record(
                operation="credit_card.profile.update",
                actor=actor,
                entity_ref=account_id,
                details={"account_id": account_id, **command.model_dump(mode="json", exclude_unset=True)},
            )
            return self._credit_card_overview(account_id, profile=profile)

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="credit_card.profile.update",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_credit_card_profile_change(result["profile"])
        return result, replay

    def _require_credit_card_account(self, account_id: str) -> Account:
        account = self.ledger.get_account(account_id)
        if account.type != "liability" or account.subtype != "credit_card":
            raise ValidationError("credit card profile requires a liability account with subtype credit_card")
        return account

    def _credit_card_overview(self, account_id: str, *, profile=None) -> dict[str, Any]:
        account = self._require_credit_card_account(account_id)
        profile = profile if profile is not None else self.credit_cards.get_optional(account_id)
        current_balance = self.ledger.balance(account_id).get(account.currency, Decimal("0"))
        credit_limit = profile.credit_limit if profile is not None else None
        derived_available_credit = credit_limit - current_balance if credit_limit is not None else None
        utilization_rate = None
        if credit_limit is not None and credit_limit > Decimal("0"):
            utilization_rate = current_balance / credit_limit
        return {
            "account": account,
            "profile": profile,
            "instruments": self.payment_instruments.list(book_id=account.book_id, account_id=account.account_id),
            "currency": account.currency,
            "current_balance": current_balance,
            "credit_limit": credit_limit,
            "available_credit": profile.available_credit if profile is not None else None,
            "derived_available_credit": derived_available_credit,
            "utilization_rate": utilization_rate,
        }
