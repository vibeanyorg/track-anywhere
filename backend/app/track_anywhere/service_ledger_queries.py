from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .books import DEFAULT_BOOK_ID
from .errors import NotFound
from .ledger import Transaction


class LedgerQueryUseCases:
    def list_transactions(
        self,
        token: str,
        *,
        account_id: str | None = None,
        category_id: str | None = None,
        counterparty: str | None = None,
        limit: int = 20,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> list[Transaction]:
        self.actor_for_book(token, book_id, "ledger:read")
        if account_id:
            account = self._get_account_from_storage(account_id)
            if account.book_id != book_id:
                return []
        if category_id:
            category = self._get_category_from_storage(category_id)
            if category.book_id != book_id:
                return []
        counterparty_id = None
        if counterparty:
            try:
                counterparty_entity = self._resolve_counterparty_reference(counterparty, book_id=book_id)
            except NotFound:
                return []
            counterparty_id = counterparty_entity.counterparty_id
        return self._list_transactions_from_storage(
            book_id=book_id,
            account_id=account_id,
            category_id=category_id,
            counterparty_id=counterparty_id,
            limit=limit,
        )

    def get_transaction(self, token: str, transaction_id: str) -> Transaction:
        transaction = self._get_transaction_from_storage(transaction_id)
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
        accounts = [self._get_account_from_storage(account_id) for account_id in sorted(account_ids)]
        return {
            "schema_version": "tx-snapshot.v1",
            "captured_at": datetime.now(timezone.utc),
            "book_id": transaction.book_id,
            "transaction": transaction,
            "accounts": accounts,
            "categories": categories,
            "category_versions": category_versions,
        }

    def _list_transactions_from_storage(
        self,
        *,
        book_id: str,
        account_id: str | None = None,
        category_id: str | None = None,
        counterparty_id: str | None = None,
        limit: int = 20,
    ) -> list[Transaction]:
        with self.storage.unit_of_work() as uow:
            return uow.transactions.list_confirmed_transactions(
                book_id=book_id,
                account_id=account_id,
                category_id=category_id,
                counterparty_id=counterparty_id,
                limit=limit,
            )

    def _get_transaction_from_storage(self, transaction_id: str) -> Transaction | None:
        with self.storage.unit_of_work() as uow:
            return uow.transactions.get_confirmed_transaction(transaction_id)

    def _list_all_transactions_from_storage(self, *, book_id: str) -> list[Transaction]:
        with self.storage.unit_of_work() as uow:
            return uow.transactions.list_all_confirmed_transactions(book_id=book_id)
