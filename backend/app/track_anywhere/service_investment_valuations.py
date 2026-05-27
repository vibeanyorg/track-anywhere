from __future__ import annotations

from typing import Any

from .domain_commands import RecordInvestmentValuationCommand
from .errors import ValidationError
from .investments import InvestmentValuation


class InvestmentValuationUseCases:
    def record_investment_valuation(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[InvestmentValuation, bool]:
        command = RecordInvestmentValuationCommand.model_validate(payload)
        account = self.storage.get_account(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "investment:write")
        if account.type != "asset":
            raise ValidationError("investment valuations can only be recorded against asset accounts")
        if account.currency != command.currency:
            raise ValidationError("investment valuation currency must match account currency")
        self.assets.validate_amount(command.currency, command.value, field_name="valuation value")
        request_hash = self._hash_command(command)

        def run():
            valuation = self.investments.record_valuation(
                book_id=account.book_id,
                account_id=command.account_id,
                value=command.value,
                currency=command.currency,
                observed_at=command.observed_at,
                source=command.source,
                memo=command.memo,
            )
            self.audit.record(operation="investment.valuation.record", actor=actor, entity_ref=valuation.valuation_id, details=command.model_dump(mode="json"))
            return valuation

        valuation, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="investment.valuation.record", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, lambda: self._commit_investment_change(valuations=(valuation,)))
        return valuation, replay

    def list_investment_valuations(self, token: str, account_id: str) -> list[InvestmentValuation]:
        account = self.storage.get_account(account_id)
        self.actor_for_book(token, account.book_id, "investment:read")
        return self.investments.list_valuations(account_id, book_id=account.book_id)
