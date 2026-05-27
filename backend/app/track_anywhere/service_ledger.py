from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .books import DEFAULT_BOOK_ID
from .commands import RecordExpenseCommand, RecordIncomeCommand, RecordTransactionCommand, ReverseTransactionCommand
from .errors import NotFound, ValidationError
from .ledger import Posting, Transaction
from .transaction_builder import build_transaction


class LedgerUseCases:
    def record_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RecordTransactionCommand.model_validate(payload)
        from_account = self.storage.get_account(command.from_account_id)
        to_account = self.storage.get_account(command.to_account_id)
        if from_account.book_id != to_account.book_id:
            raise ValidationError("transaction accounts must belong to one book")
        if from_account.currency != command.currency or to_account.currency != command.currency:
            raise ValidationError("transaction currency must match both account currencies")
        self.assets.validate_amount(command.currency, command.amount)
        actor = self.actor_for_book(token, from_account.book_id, "ledger:confirm")
        if command.category_id is not None:
            category = self.storage.get_category(command.category_id)
            if category.book_id != from_account.book_id:
                raise ValidationError("transaction category must belong to the same book")
            self._validate_transaction_category(
                category,
                from_account_id=command.from_account_id,
                to_account_id=command.to_account_id,
            )
        request_hash = self._hash_command(command)

        def run():
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.from_account_id, -command.amount, command.currency),
                    Posting(command.to_account_id, command.amount, command.currency),
                ],
                accounts=[from_account, to_account],
                scale_lookup=self.assets.scale_for,
            )
            if command.category_id is not None:
                self._add_category_line_for_transaction(transaction, self.storage.get_category(command.category_id))
            self.ledger.transactions[transaction.transaction_id] = transaction
            self.audit.record(
                operation="ledger.transaction.record",
                actor=actor,
                entity_ref=transaction.transaction_id,
                details=command.model_dump(mode="json"),
            )
            return transaction

        transaction, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.transaction.record",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(transaction)
        return transaction, replay

    def record_expense(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RecordExpenseCommand.model_validate(payload)
        source = self.storage.get_account(command.from_account_id)
        actor = self.actor_for_book(token, source.book_id, "ledger:confirm")
        if source.currency != command.currency:
            raise ValidationError("expense currency must match source account currency")
        self.assets.validate_amount(command.currency, command.amount)
        category = self.storage.get_category(command.category_id)
        if category.book_id != source.book_id:
            raise ValidationError("expense category must belong to the same book")
        if category.kind != "expense":
            raise ValidationError("expense record requires an expense category")
        request_hash = self._hash_command(command)

        def run():
            expense_account_id = self._system_category_account_id("expense", command.currency, book_id=source.book_id)
            expense_account = self._transaction_account(expense_account_id)
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(command.from_account_id, -command.amount, command.currency),
                    Posting(expense_account_id, command.amount, command.currency),
                ],
                accounts=[source, expense_account],
                scale_lookup=self.assets.scale_for,
            )
            self._add_category_line_for_transaction(transaction, category)
            self.ledger.transactions[transaction.transaction_id] = transaction
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
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(transaction)
        return transaction, replay

    def record_income(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = RecordIncomeCommand.model_validate(payload)
        target = self.storage.get_account(command.to_account_id)
        actor = self.actor_for_book(token, target.book_id, "ledger:confirm")
        if target.currency != command.currency:
            raise ValidationError("income currency must match target account currency")
        self.assets.validate_amount(command.currency, command.amount)
        category = self.storage.get_category(command.category_id)
        if category.book_id != target.book_id:
            raise ValidationError("income category must belong to the same book")
        if category.kind != "income":
            raise ValidationError("income record requires an income category")
        request_hash = self._hash_command(command)

        def run():
            income_account_id = self._system_category_account_id("income", command.currency, book_id=target.book_id)
            income_account = self._transaction_account(income_account_id)
            transaction = build_transaction(
                memo=command.memo,
                occurred_at=command.occurred_at,
                purpose=command.purpose,
                postings=[
                    Posting(income_account_id, -command.amount, command.currency),
                    Posting(command.to_account_id, command.amount, command.currency),
                ],
                accounts=[income_account, target],
                scale_lookup=self.assets.scale_for,
            )
            self._add_category_line_for_transaction(transaction, category)
            self.ledger.transactions[transaction.transaction_id] = transaction
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
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(transaction)
        return transaction, replay

    def list_transactions(
        self,
        token: str,
        *,
        account_id: str | None = None,
        category_id: str | None = None,
        limit: int = 20,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> list[Transaction]:
        self.actor_for_book(token, book_id, "ledger:read")
        if account_id:
            account = self.storage.get_account(account_id)
            if account.book_id != book_id:
                return []
        if category_id:
            category = self.storage.get_category(category_id)
            if category.book_id != book_id:
                return []
        return self.storage.list_confirmed_transactions(
            book_id=book_id,
            account_id=account_id,
            category_id=category_id,
            limit=limit,
        )

    def get_transaction(self, token: str, transaction_id: str) -> Transaction:
        transaction = self.storage.get_confirmed_transaction(transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {transaction_id}")
        self.actor_for_book(token, transaction.book_id, "ledger:read")
        return transaction

    def transaction_snapshot(self, token: str, transaction_id: str) -> dict[str, Any]:
        transaction = self.get_transaction(token, transaction_id)
        account_ids = {posting.account_id for posting in transaction.postings}
        category_ids = {line.category_id for line in transaction.lines if line.category_id is not None}
        category_version_ids = {
            line.category_version_id
            for line in transaction.lines
            if line.category_version_id is not None
        }
        categories = [
            self.categories.get(category_id)
            for category_id in sorted(category_ids)
            if category_id in self.categories.categories
        ]
        category_versions = [
            version
            for version in self.categories.versions.values()
            if version.category_id in category_ids or version.category_version_id in category_version_ids
        ]
        category_versions.sort(key=lambda version: (version.category_id, version.valid_from, version.category_version_id))
        accounts = [
            self.ledger.accounts[account_id]
            for account_id in sorted(account_ids)
            if account_id in self.ledger.accounts
        ]
        return {
            "schema_version": "tx-snapshot.v1",
            "captured_at": datetime.now(timezone.utc),
            "book_id": transaction.book_id,
            "transaction": transaction,
            "accounts": accounts,
            "categories": categories,
            "category_versions": category_versions,
        }

    def reverse_transaction(self, token: str, payload: dict[str, Any], *, idempotency_key: str):
        command = ReverseTransactionCommand.model_validate(payload)
        transaction = self.storage.get_confirmed_transaction(command.transaction_id)
        if transaction is None:
            raise NotFound(f"transaction not found: {command.transaction_id}")
        if transaction.reversed_by is not None:
            raise ValidationError("transaction is already reversed")
        if transaction.reverses_transaction_id is not None:
            raise ValidationError("reversal transactions cannot be reversed directly")
        actor = self.actor_for_book(token, transaction.book_id, "ledger:reverse")
        request_hash = self._hash_command(command)

        def run():
            reversal = build_transaction(
                memo=command.memo,
                purpose="reversal",
                postings=[
                    Posting(account_id=posting.account_id, amount=-posting.amount, currency=posting.currency)
                    for posting in transaction.postings
                ],
                book_id=transaction.book_id,
                reverses_transaction_id=transaction.transaction_id,
                accounts=[self._transaction_account(posting.account_id) for posting in transaction.postings],
                scale_lookup=self.assets.scale_for,
            )
            transaction.reversed_by = reversal.transaction_id
            legacy_transaction = self.ledger.transactions.get(transaction.transaction_id)
            if legacy_transaction is not None:
                legacy_transaction.reversed_by = reversal.transaction_id
                self.ledger.transactions[transaction.transaction_id] = legacy_transaction
            else:
                self.ledger.transactions[transaction.transaction_id] = transaction
            self.ledger.transactions[reversal.transaction_id] = reversal
            self.audit.record(
                operation="ledger.reverse",
                actor=actor,
                entity_ref=command.transaction_id,
                details={"reversal_transaction_id": reversal.transaction_id},
            )
            return reversal

        reversal, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="ledger.reverse",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_ledger_change(transaction, reversal)
        return reversal, replay
