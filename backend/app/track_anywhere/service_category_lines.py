from __future__ import annotations

from decimal import Decimal

from .accounting import validate_posting_semantic_shape
from .categories import Category
from .errors import NotFound, ValidationError
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
            category_amount = _posting_category_amount(posting, category.kind)
            if category_amount is None:
                continue
            if category.kind == "expense" and account.type == "expense":
                amounts[posting.currency] = amounts.get(posting.currency, Decimal("0")) + category_amount
            elif category.kind == "income" and account.type == "income":
                amounts[posting.currency] = amounts.get(posting.currency, Decimal("0")) + category_amount
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


def _posting_category_amount(posting, category_kind: str) -> Decimal | None:
    try:
        validate_posting_semantic_shape(
            side=posting.side,
            amount=posting.amount,
            amount_semantics=posting.amount_semantics,
        )
    except ValidationError:
        return None
    if posting.amount_semantics == "debit_credit":
        if category_kind == "expense" and posting.side == "debit":
            return posting.amount
        if category_kind == "income" and posting.side == "credit":
            return posting.amount
        return None
    if posting.amount_semantics == "legacy_signed":
        if category_kind == "expense" and posting.amount > Decimal("0"):
            return posting.amount
        if category_kind == "income" and posting.amount < Decimal("0"):
            return -posting.amount
    return None
