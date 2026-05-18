from __future__ import annotations

from typing import Any

from .commands import RecordExpenseCommand, RecordIncomeCommand, RecordTransactionCommand, ReverseTransactionCommand
from .errors import NotFound, ValidationError
from .ledger import Posting, Transaction


class LedgerUseCases:
    def record_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = RecordTransactionCommand.model_validate(payload)
        if command.category_id is not None:
            category = self.categories.get(command.category_id)
            self._validate_transaction_category(
                category,
                from_account_id=command.from_account_id,
                to_account_id=command.to_account_id,
            )
        request_hash = self._hash_command(command)

        def run():
            transaction = self.ledger.create_transaction(
                memo=command.purpose,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                category_id=command.category_id,
                postings=[
                    Posting(command.from_account_id, -command.amount, command.currency),
                    Posting(command.to_account_id, command.amount, command.currency),
                ],
            )
            self.audit.record(
                operation="ledger.transaction.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=command.model_dump(mode="json"),
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.transaction.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_expense(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = RecordExpenseCommand.model_validate(payload)
        source = self.ledger.get_account(command.from_account_id)
        if source.currency != command.currency:
            raise ValidationError("expense currency must match source account currency")
        category = self.categories.get(command.category_id)
        if category.kind != "expense":
            raise ValidationError("expense record requires an expense category")
        request_hash = self._hash_command(command)

        def run():
            expense_account_id = self._system_category_account_id("expense", command.currency)
            transaction = self.ledger.create_transaction(
                memo=command.purpose,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                category_id=command.category_id,
                postings=[
                    Posting(command.from_account_id, -command.amount, command.currency),
                    Posting(expense_account_id, command.amount, command.currency),
                ],
            )
            self.audit.record(
                operation="expense.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details={**command.model_dump(mode="json"), "expense_account_id": expense_account_id},
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="expense.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def record_income(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:confirm")
        command = RecordIncomeCommand.model_validate(payload)
        target = self.ledger.get_account(command.to_account_id)
        if target.currency != command.currency:
            raise ValidationError("income currency must match target account currency")
        category = self.categories.get(command.category_id)
        if category.kind != "income":
            raise ValidationError("income record requires an income category")
        request_hash = self._hash_command(command)

        def run():
            income_account_id = self._system_category_account_id("income", command.currency)
            transaction = self.ledger.create_transaction(
                memo=command.purpose,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                category_id=command.category_id,
                postings=[
                    Posting(income_account_id, -command.amount, command.currency),
                    Posting(command.to_account_id, command.amount, command.currency),
                ],
            )
            self.audit.record(
                operation="income.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details={**command.model_dump(mode="json"), "income_account_id": income_account_id},
            )
            return transaction

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="income.record",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def list_transactions(
        self,
        token: str,
        *,
        account_id: str | None = None,
        category_id: str | None = None,
        limit: int = 20,
    ) -> list[Transaction]:
        self.actor_from_token(token, "ledger:read")
        if account_id:
            self.ledger.get_account(account_id)
        if category_id:
            self.categories.get(category_id)
        transactions = list(self.ledger.transactions.values())
        if account_id:
            transactions = [
                transaction
                for transaction in transactions
                if any(posting.account_id == account_id for posting in transaction.postings)
            ]
        if category_id:
            transactions = [transaction for transaction in transactions if transaction.category_id == category_id]
        transactions.sort(key=lambda transaction: (transaction.occurred_at, transaction.transaction_id), reverse=True)
        return transactions[: max(0, min(limit, 200))]

    def get_transaction(self, token: str, transaction_id: str) -> Transaction:
        self.actor_from_token(token, "ledger:read")
        transaction = self.ledger.transactions.get(transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {transaction_id}")
        return transaction

    def reverse_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "ledger:reverse")
        command = ReverseTransactionCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            reversal = self.ledger.reverse_transaction(command.transaction_id, command.memo)
            self.audit.record(
                operation="ledger.reverse",
                actor=actor,
                entity_ref=command.transaction_id,
                details={"reversal_transaction_id": reversal.transaction_id},
            )
            return reversal

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.reverse",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result
