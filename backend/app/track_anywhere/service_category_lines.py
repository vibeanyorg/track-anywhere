from __future__ import annotations

from decimal import Decimal

from .categories import Category
from .errors import NotFound
from .transaction_builder import add_transaction_line


class CategoryLineUseCases:
    def _category_amounts_for_transaction(self, transaction, category: Category, *, accounts=()) -> dict[str, Decimal]:
        amounts: dict[str, Decimal] = {}
        accounts_by_id = {account.account_id: account for account in accounts}
        for posting in transaction.postings:
            account = accounts_by_id.get(posting.account_id)
            if account is None:
                try:
                    account = self._transaction_account(posting.account_id)
                except NotFound:
                    continue
            if category.kind == "expense" and account.type == "expense" and posting.amount > Decimal("0"):
                amounts[posting.currency] = amounts.get(posting.currency, Decimal("0")) + posting.amount
            elif category.kind == "income" and account.type == "income" and posting.amount < Decimal("0"):
                amounts[posting.currency] = amounts.get(posting.currency, Decimal("0")) - posting.amount
        return amounts

    def _add_category_line_for_transaction(self, transaction, category: Category, *, accounts=(), counterparty_id=None):
        amounts = self._category_amounts_for_transaction(transaction, category, accounts=accounts)
        version = self.categories.active_version(category.category_id)
        category_path_snapshot = {
            "category_id": category.category_id,
            "category_version_id": version.category_version_id,
            "kind": category.kind,
            "primary": category.primary,
            "secondary": category.secondary,
            "path": category.path_cache,
        }
        for currency, amount in amounts.items():
            if amount > Decimal("0"):
                add_transaction_line(
                    transaction,
                    line_type=category.kind,
                    amount=amount,
                    currency=currency,
                    category_id=category.category_id,
                    category_version_id=version.category_version_id,
                    category_path_snapshot=category_path_snapshot,
                    counterparty_id=counterparty_id,
                    memo=transaction.memo,
                    scale_lookup=self.assets.scale_for,
                )
