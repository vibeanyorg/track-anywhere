from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain_commands import RecordFxExchangeCommand
from .errors import ValidationError
from .ledger import Account, Transaction, credit_posting, debit_posting
from .transaction_builder import add_transaction_line, build_transaction


@dataclass(frozen=True)
class FxExchangeContext:
    from_account: Account
    to_account: Account
    fee_account: Account | None = None


class FxUseCases:
    def record_fx_exchange(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Transaction, bool]:
        command = RecordFxExchangeCommand.model_validate(payload)
        context = self._fx_exchange_context(command)
        actor = self.actor_for_book(token, context.from_account.book_id, "ledger:confirm")
        request_hash = self._hash_command(command)
        created_accounts: list[Account] = []

        def create_fx_exchange_transaction() -> Transaction:
            return self._create_fx_exchange_transaction(command, context, actor, created_accounts=created_accounts)

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.fx.exchange",
            request_hash=request_hash,
            fn=create_fx_exchange_transaction,
        )
        self._commit_replay_or(
            replay,
            lambda: self._commit_ledger_change(transaction, accounts=created_accounts),
        )
        return transaction, replay

    def _fx_exchange_context(self, command: RecordFxExchangeCommand) -> FxExchangeContext:
        from_account = self._get_account_from_storage(command.from_account_id)
        to_account = self._get_account_from_storage(command.to_account_id)
        if from_account.book_id != to_account.book_id:
            raise ValidationError("FX exchange accounts must belong to one book")
        if from_account.currency != command.from_currency:
            raise ValidationError("from_currency must match source account currency")
        if to_account.currency != command.to_currency:
            raise ValidationError("to_currency must match target account currency")
        self.assets.validate_amount(command.from_currency, command.from_amount, field_name="from_amount")
        self.assets.validate_amount(command.to_currency, command.to_amount, field_name="to_amount")
        fee_account = self._validated_fx_fee_account(command, book_id=from_account.book_id)
        return FxExchangeContext(from_account=from_account, to_account=to_account, fee_account=fee_account)

    def _validated_fx_fee_account(self, command: RecordFxExchangeCommand, *, book_id: str) -> Account | None:
        if command.fee_account_id is None and command.fee_amount is None:
            return None
        if command.fee_account_id is None or command.fee_amount is None:
            raise ValidationError("fee_account_id and fee_amount must be provided together")
        fee_account = self._get_account_from_storage(command.fee_account_id)
        if fee_account.book_id != book_id:
            raise ValidationError("FX fee account must belong to the exchange book")
        if fee_account.currency != command.from_currency:
            raise ValidationError("FX fee is currently supported only in from_currency")
        if fee_account.type not in {"expense", "system"}:
            raise ValidationError("FX fee account must be an expense or system account")
        self.assets.validate_amount(command.from_currency, command.fee_amount, field_name="fee_amount")
        return fee_account

    def _create_fx_exchange_transaction(
        self,
        command: RecordFxExchangeCommand,
        context: FxExchangeContext,
        actor: Any,
        *,
        created_accounts: list[Account],
    ) -> Transaction:
        from_clearing_account = self._system_fx_clearing_account(
            command.from_currency,
            book_id=context.from_account.book_id,
            created_accounts=created_accounts,
        )
        to_clearing_account = self._system_fx_clearing_account(
            command.to_currency,
            book_id=context.from_account.book_id,
            created_accounts=created_accounts,
        )
        accounts = [context.from_account, context.to_account, from_clearing_account, to_clearing_account]
        postings = [
            credit_posting(command.from_account_id, command.from_amount, command.from_currency),
            debit_posting(from_clearing_account.account_id, command.from_amount, command.from_currency),
            debit_posting(command.to_account_id, command.to_amount, command.to_currency),
            credit_posting(to_clearing_account.account_id, command.to_amount, command.to_currency),
        ]
        if context.fee_account is not None and command.fee_amount is not None:
            accounts.append(context.fee_account)
            postings.extend(
                [
                    credit_posting(command.from_account_id, command.fee_amount, command.from_currency),
                    debit_posting(context.fee_account.account_id, command.fee_amount, command.from_currency),
                ]
            )

        transaction = build_transaction(
            memo=command.memo,
            occurred_at=command.occurred_at,
            purpose=command.purpose,
            postings=postings,
            book_id=context.from_account.book_id,
            accounts=accounts,
            scale_lookup=self.assets.scale_for,
        )
        add_transaction_line(transaction, line_type="fx_exchange", amount=command.from_amount, currency=command.from_currency, memo=command.memo, scale_lookup=self.assets.scale_for)
        if command.fee_amount is not None:
            add_transaction_line(transaction, line_type="fx_fee", amount=command.fee_amount, currency=command.from_currency, memo=command.memo, scale_lookup=self.assets.scale_for)
        self.audit.record(operation="ledger.fx.exchange", actor=actor, entity_ref=transaction.transaction_id, details=command.model_dump(mode="json"))
        return transaction
