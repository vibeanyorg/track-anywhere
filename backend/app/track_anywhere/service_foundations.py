from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .ledger import Ledger


class DomainFoundationBootstrap:
    def _ensure_domain_foundations(self) -> None:
        self.books.ensure_default()
        self.assets.ensure_defaults()
        for book in self.books.books.values():
            self.assets.ensure(book.base_currency)
        accounts = self.storage.list_accounts(book_id=None)
        accounts_by_id = {account.account_id: account for account in accounts}
        validator = Ledger(asset_scale_lookup=self.assets.scale_for)
        validator.accounts = accounts_by_id
        for account in accounts:
            self.assets.ensure(account.currency)
        self._ensure_category_foundations()
        self._ensure_transaction_foundations(accounts_by_id, validator)
        self._ensure_draft_foundations(accounts_by_id)
        self._ensure_recurring_foundations()
        self._ensure_investment_foundations(accounts_by_id)
        for budget in self.budgets.budgets.values():
            self.assets.ensure(budget.currency)

    def _ensure_category_foundations(self) -> None:
        for category in list(self.categories.categories.values()):
            category.book_id = category.book_id or DEFAULT_BOOK_ID
            self.categories._sync_display_fields(category)
            if not any(version.category_id == category.category_id for version in self.categories.versions.values()):
                self.categories._record_version(category, "migration")

    def _ensure_transaction_foundations(self, accounts_by_id: dict[str, Any], validator: Ledger) -> None:
        for book in self.books.books.values():
            for transaction in self.storage.list_all_confirmed_transactions(book_id=book.book_id):
                self._ensure_transaction_foundation(transaction, accounts_by_id, validator)

    def _ensure_draft_foundations(self, accounts_by_id: dict[str, Any]) -> None:
        for draft in self.drafts.drafts.values():
            draft.book_id = draft.book_id or self._book_id_for_postings(draft.proposed_postings, accounts_by_id)

    def _ensure_recurring_foundations(self) -> None:
        for item in self.recurring.items.values():
            if not item.book_id and item.source_account_id is not None:
                item.book_id = self.storage.get_account(item.source_account_id).book_id
            if item.currency is not None:
                self.assets.ensure(item.currency)

    def _ensure_investment_foundations(self, accounts_by_id: dict[str, Any]) -> None:
        for event in self.investments.events.values():
            account = accounts_by_id.get(event.account_id)
            if account is not None:
                event.book_id = event.book_id or account.book_id
            self.assets.ensure(event.currency)
        for valuation in self.investments.valuations.values():
            account = accounts_by_id.get(valuation.account_id)
            if account is not None:
                valuation.book_id = valuation.book_id or account.book_id
            self.assets.ensure(valuation.currency)

    def _ensure_transaction_foundation(self, transaction, accounts_by_id: dict[str, Any], validator: Ledger) -> None:
        transaction.book_id = transaction.book_id or self._book_id_for_postings(transaction.postings, accounts_by_id)
        if transaction.lines is None:
            transaction.lines = []
        for posting in transaction.postings:
            self.assets.ensure(posting.currency)
        for line in transaction.lines:
            self.assets.ensure(line.currency)
        validator.validate_transaction_integrity(transaction, enforce_asset_scale=False)

    @staticmethod
    def _book_id_for_postings(postings, accounts_by_id: dict[str, Any]) -> str:
        for posting in postings:
            account = accounts_by_id.get(posting.account_id)
            if account is not None:
                return account.book_id
        return DEFAULT_BOOK_ID
