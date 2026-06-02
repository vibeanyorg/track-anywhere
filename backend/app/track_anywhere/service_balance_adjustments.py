from __future__ import annotations

from typing import Any

from .commands import BalanceAdjustmentCommand
from .errors import ValidationError
from .ledger import debit_credit_posting_for_balance_delta, opposite_side_posting
from .transaction_builder import build_transaction


def _balance_adjustment_postings(account, adjustment_account_id: str, amount, currency: str):
    account_posting = debit_credit_posting_for_balance_delta(account.account_id, account.type, amount, currency)
    adjustment_posting = opposite_side_posting(adjustment_account_id, account_posting.side, abs(amount), currency)
    return [account_posting, adjustment_posting]


class BalanceAdjustmentUseCases:
    def adjust_balance(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = BalanceAdjustmentCommand.model_validate(payload)
        account = self._get_account_from_storage(command.account_id)
        actor = self.actor_for_book(token, account.book_id, "ledger:confirm")
        self.assets.validate_amount(command.currency, command.amount)
        request_hash = self._hash_command(command)
        created_accounts = []

        def run():
            if account.currency != command.currency:
                raise ValidationError("balance adjustment currency must match account currency")
            adjustment_account = self._system_adjustment_account(
                command.currency,
                book_id=account.book_id,
                created_accounts=created_accounts,
            )
            adjustment_account_id = adjustment_account.account_id
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=_balance_adjustment_postings(account, adjustment_account_id, command.amount, command.currency),
                accounts=[account, adjustment_account],
                scale_lookup=self.assets.scale_for,
            )
            self.audit.record(
                operation="ledger.balance.adjust",
                actor=actor,
                entity_ref=command.account_id,
                details={
                    **command.model_dump(mode="json"),
                    "transaction_id": transaction.transaction_id,
                    "offset_account_id": adjustment_account_id,
                },
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.balance.adjust",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction, accounts=created_accounts))
        return transaction, replay
