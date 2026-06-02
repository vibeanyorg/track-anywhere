from __future__ import annotations

from typing import Any

from .commands import RecordExpenseCommand, RecordIncomeCommand
from .errors import ValidationError
from .ledger import credit_posting, debit_posting
from .transaction_builder import build_transaction


class LedgerRecordUseCases:
    def record_expense(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RecordExpenseCommand.model_validate(payload)
        source = self._get_account_from_storage(command.from_account_id)
        actor = self.actor_for_book(token, source.book_id, "ledger:confirm")
        if source.currency != command.currency:
            raise ValidationError("expense currency must match source account currency")
        self.assets.validate_amount(command.currency, command.amount)
        category = self._get_category_from_storage(command.category_id)
        if category.book_id != source.book_id:
            raise ValidationError("expense category must belong to the same book")
        if category.kind != "expense":
            raise ValidationError("expense record requires an expense category")
        request_hash = self._hash_command(command)
        created_accounts = []

        def run():
            counterparty = self._resolve_counterparty_for_write(command.counterparty, book_id=source.book_id)
            expense_account = self._system_category_account(
                "expense",
                command.currency,
                book_id=source.book_id,
                created_accounts=created_accounts,
            )
            expense_account_id = expense_account.account_id
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    credit_posting(command.from_account_id, command.amount, command.currency),
                    debit_posting(expense_account_id, command.amount, command.currency),
                ],
                accounts=[source, expense_account],
                scale_lookup=self.assets.scale_for,
            )
            self._add_category_line_for_transaction(
                transaction,
                category,
                accounts=(source, expense_account),
                counterparty_id=counterparty.counterparty_id if counterparty else None,
            )
            self.audit.record(
                operation="expense.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details={**command.model_dump(mode="json"), "expense_account_id": expense_account_id},
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="expense.record",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction, accounts=created_accounts))
        return transaction, replay

    def record_income(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RecordIncomeCommand.model_validate(payload)
        target = self._get_account_from_storage(command.to_account_id)
        actor = self.actor_for_book(token, target.book_id, "ledger:confirm")
        if target.currency != command.currency:
            raise ValidationError("income currency must match target account currency")
        self.assets.validate_amount(command.currency, command.amount)
        category = self._get_category_from_storage(command.category_id)
        if category.book_id != target.book_id:
            raise ValidationError("income category must belong to the same book")
        if category.kind != "income":
            raise ValidationError("income record requires an income category")
        request_hash = self._hash_command(command)
        created_accounts = []

        def run():
            counterparty = self._resolve_counterparty_for_write(command.counterparty, book_id=target.book_id)
            income_account = self._system_category_account(
                "income",
                command.currency,
                book_id=target.book_id,
                created_accounts=created_accounts,
            )
            income_account_id = income_account.account_id
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    credit_posting(income_account_id, command.amount, command.currency),
                    debit_posting(command.to_account_id, command.amount, command.currency),
                ],
                accounts=[income_account, target],
                scale_lookup=self.assets.scale_for,
            )
            self._add_category_line_for_transaction(
                transaction,
                category,
                accounts=(income_account, target),
                counterparty_id=counterparty.counterparty_id if counterparty else None,
            )
            self.audit.record(
                operation="income.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details={**command.model_dump(mode="json"), "income_account_id": income_account_id},
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="income.record",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(replay, lambda: self._commit_ledger_change(transaction, accounts=created_accounts))
        return transaction, replay
