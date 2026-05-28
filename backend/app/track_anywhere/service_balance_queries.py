from __future__ import annotations

from decimal import Decimal
from typing import Any


class BalanceQueryUseCases:
    def account_balance(self, token: str, account_id: str, *, include_drafts: bool = False) -> dict[str, Any]:
        account = self._get_account_from_storage(account_id)
        self.actor_for_book(token, account.book_id, "account:read")
        official = self.storage.account_balance(account_id)
        pending: dict[str, Decimal] = {}
        included_draft_ids: list[str] = []
        draft_count = self.storage.draft_count()
        if include_drafts:
            pending, included_draft_ids, draft_count = self.storage.draft_projection_for_account(account_id)
        currency = account.currency
        official_amount = official.get(currency, Decimal("0"))
        pending_amount = pending.get(currency, Decimal("0"))
        result = {
            "account_id": account_id,
            "currency": currency,
            "official_balance": {
                "amount": str(official_amount),
                "source": "confirmed_postings",
                "as_of_ledger_version": self.storage.confirmed_transaction_count(book_id=account.book_id),
            },
            "default_view": "official",
            "provenance": {
                "confirmed_transaction_count": self.storage.confirmed_transaction_count(book_id=account.book_id),
                "draft_count": draft_count,
            },
        }
        if include_drafts:
            result["projected_balance"] = {
                "amount": str(official_amount + pending_amount),
                "pending_impact": str(pending_amount),
                "included_draft_ids": included_draft_ids,
                "projection_version": draft_count,
            }
        return result
