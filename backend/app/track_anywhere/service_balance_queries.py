from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from .balance_semantics import balance_delta_semantics_for_account_type, balance_semantics_for_account_type, liability_balance_view
from .ledger import Account
from .service_balance_views import official_account_balance_view


class BalanceQueryUseCases:
    def account_balance(self, token: str, account_id: str, *, include_drafts: bool = False) -> dict[str, Any]:
        account = self._get_account_from_storage(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        official = self._account_balance_from_storage(account_id)
        pending: dict[str, Decimal] = {}
        included_draft_ids: list[str] = []
        draft_count = self._draft_count_from_storage()
        if include_drafts:
            pending, included_draft_ids, draft_count = self.storage.draft_projection_for_account(account_id)
        currency = account.currency
        official_amount = official.get(currency, Decimal("0"))
        pending_amount = pending.get(currency, Decimal("0"))
        result = official_account_balance_view(
            account,
            official_amount,
            confirmed_transaction_count=self._confirmed_transaction_count_from_storage(book_id=account.book_id),
            draft_count=draft_count,
        )
        if include_drafts:
            balance_semantics = balance_semantics_for_account_type(account.type)
            result["projected_balance"] = {
                "amount": str(official_amount + pending_amount),
                "amount_semantics": balance_semantics,
                "pending_impact": str(pending_amount),
                "pending_impact_semantics": balance_delta_semantics_for_account_type(account.type),
                "included_draft_ids": included_draft_ids,
                "projection_version": draft_count,
            }
        if account.type == "liability" and include_drafts:
            result["projected_liability_balance"] = liability_balance_view(official_amount + pending_amount)
        return result

    def _official_account_balance_view(
        self,
        account: Account,
        official_amount: Decimal,
        *,
        confirmed_transaction_count: int,
        draft_count: int,
    ) -> dict[str, Any]:
        return official_account_balance_view(
            account,
            official_amount,
            confirmed_transaction_count=confirmed_transaction_count,
            draft_count=draft_count,
        )

    def _account_balance_from_storage(self, account_id: str) -> dict[str, Decimal]:
        return self.storage.account_balance(account_id)

    def _account_balances_from_storage(self, account_ids: Iterable[str]) -> dict[tuple[str, str], Decimal]:
        return self.storage.account_balances(account_ids)

    def _confirmed_transaction_count_from_storage(self, *, book_id: str | None = None) -> int:
        return self.storage.confirmed_transaction_count(book_id=book_id)

    def _draft_count_from_storage(self) -> int:
        return self.storage.draft_count()
