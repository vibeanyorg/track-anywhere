from __future__ import annotations

from typing import Any

from .domain_commands import RecordFxExchangeCommand
from .errors import ValidationError
from .ledger import Posting, Transaction
from .transaction_builder import add_transaction_line, build_transaction


class FxUseCases:
    def record_fx_exchange(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Transaction, bool]:
        command = RecordFxExchangeCommand.model_validate(payload)
        source = self.storage.get_account(command.from_account_id)
        target = self.storage.get_account(command.to_account_id)
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
        created_accounts = []

        def run():
            from_clearing_account = self._system_fx_clearing_account(
                command.from_currency,
                book_id=source.book_id,
                created_accounts=created_accounts,
            )
            to_clearing_account = self._system_fx_clearing_account(
                command.to_currency,
                book_id=source.book_id,
                created_accounts=created_accounts,
            )
            from_clearing_account_id = from_clearing_account.account_id
            to_clearing_account_id = to_clearing_account.account_id
            accounts = [
                source,
                target,
                from_clearing_account,
                to_clearing_account,
            ]
            postings = [
                Posting(command.from_account_id, -command.from_amount, command.from_currency),
                Posting(
                    from_clearing_account_id,
                    command.from_amount,
                    command.from_currency,
                ),
                Posting(command.to_account_id, command.to_amount, command.to_currency),
                Posting(
                    to_clearing_account_id,
                    -command.to_amount,
                    command.to_currency,
                ),
            ]
            if command.fee_amount is not None and command.fee_account_id is not None:
                fee_account = self.storage.get_account(command.fee_account_id)
                if fee_account.book_id != source.book_id:
                    raise ValidationError("FX fee account must belong to the exchange book")
                if fee_account.currency != command.from_currency:
                    raise ValidationError("FX fee is currently supported only in from_currency")
                if fee_account.type not in {"expense", "system"}:
                    raise ValidationError("FX fee account must be an expense or system account")
                self.assets.validate_amount(command.from_currency, command.fee_amount, field_name="fee_amount")
                accounts.append(fee_account)
                postings.extend(
                    [
                        Posting(command.from_account_id, -command.fee_amount, command.from_currency),
                        Posting(command.fee_account_id, command.fee_amount, command.from_currency),
                    ]
                )
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=postings,
                book_id=source.book_id,
                accounts=accounts,
                scale_lookup=self.assets.scale_for,
            )
            add_transaction_line(
                transaction,
                line_type="fx_exchange",
                amount=command.from_amount,
                currency=command.from_currency,
                memo=command.memo,
                scale_lookup=self.assets.scale_for,
            )
            if command.fee_amount is not None:
                add_transaction_line(
                    transaction,
                    line_type="fx_fee",
                    amount=command.fee_amount,
                    currency=command.from_currency,
                    memo=command.memo,
                    scale_lookup=self.assets.scale_for,
                )
            self.audit.record(
                operation="ledger.fx.exchange",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=command.model_dump(mode="json"),
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.fx.exchange",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(transaction, accounts=created_accounts)
        return transaction, replay
