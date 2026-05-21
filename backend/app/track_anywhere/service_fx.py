from __future__ import annotations

from typing import Any

from .domain_commands import RecordFxExchangeCommand
from .errors import ValidationError
from .ledger import Posting, Transaction


class FxUseCases:
    def record_fx_exchange(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Transaction, bool]:
        command = RecordFxExchangeCommand.model_validate(payload)
        source = self.ledger.get_account(command.from_account_id)
        target = self.ledger.get_account(command.to_account_id)
        if source.book_id != target.book_id:
            raise ValidationError("FX exchange accounts must belong to one book")
        if source.currency != command.from_currency:
            raise ValidationError("from_currency must match source account currency")
        if target.currency != command.to_currency:
            raise ValidationError("to_currency must match target account currency")
        self.assets.validate_amount(command.from_currency, command.from_amount, field_name="from_amount")
        self.assets.validate_amount(command.to_currency, command.to_amount, field_name="to_amount")
        actor = self.actor_for_book(token, source.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)

        def run():
            postings = [
                Posting(command.from_account_id, -command.from_amount, command.from_currency),
                Posting(
                    self._system_fx_clearing_account_id(command.from_currency, book_id=source.book_id),
                    command.from_amount,
                    command.from_currency,
                ),
                Posting(command.to_account_id, command.to_amount, command.to_currency),
                Posting(
                    self._system_fx_clearing_account_id(command.to_currency, book_id=source.book_id),
                    -command.to_amount,
                    command.to_currency,
                ),
            ]
            if command.fee_amount is not None and command.fee_account_id is not None:
                fee_account = self.ledger.get_account(command.fee_account_id)
                if fee_account.book_id != source.book_id:
                    raise ValidationError("FX fee account must belong to the exchange book")
                if fee_account.currency != command.from_currency:
                    raise ValidationError("FX fee is currently supported only in from_currency")
                if fee_account.type not in {"expense", "system"}:
                    raise ValidationError("FX fee account must be an expense or system account")
                self.assets.validate_amount(command.from_currency, command.fee_amount, field_name="fee_amount")
                postings.extend(
                    [
                        Posting(command.from_account_id, -command.fee_amount, command.from_currency),
                        Posting(command.fee_account_id, command.fee_amount, command.from_currency),
                    ]
                )
            transaction = self.ledger.create_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=postings,
                book_id=source.book_id,
            )
            self.ledger.add_line(
                transaction,
                line_type="fx_exchange",
                amount=command.from_amount,
                currency=command.from_currency,
                memo=command.memo,
            )
            if command.fee_amount is not None:
                self.ledger.add_line(
                    transaction,
                    line_type="fx_fee",
                    amount=command.fee_amount,
                    currency=command.from_currency,
                    memo=command.memo,
                )
            self.audit.record(
                operation="ledger.fx.exchange",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=command.model_dump(mode="json"),
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.fx.exchange",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def _system_fx_clearing_account_id(self, currency: str, *, book_id: str) -> str:
        for account in self.ledger.accounts.values():
            if (
                account.book_id == book_id
                and account.type == "system"
                and account.currency == currency
                and account.institution_type == "system"
                and account.subtype == "fx_clearing"
                and account.institution == "track-anywhere"
            ):
                return account.account_id
        account = self.ledger.create_account(
            f"System FX clearing {currency}",
            "system",
            currency,
            institution_type="system",
            subtype="fx_clearing",
            institution="track-anywhere",
            book_id=book_id,
        )
        return account.account_id
