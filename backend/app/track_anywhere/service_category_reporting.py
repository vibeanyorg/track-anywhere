from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .errors import ValidationError


class CategoryReportingUseCases:
    def category_summary(
        self,
        token: str,
        *,
        kind: str | None = None,
        currency: str | None = None,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> dict[str, Any]:
        self.actor_for_book(token, book_id, "ledger:read")
        if kind is not None and kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")

        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for transaction in self.storage.list_all_confirmed_transactions(book_id=book_id):
            if transaction.book_id != book_id or transaction.reversed_by is not None:
                continue
            for line in self._report_lines_for_transaction(transaction):
                if line.category_id is None:
                    continue
                category = self.categories.get(line.category_id)
                line_kind = "expense" if line.line_type in {"expense", "transfer_fee"} else line.line_type
                if kind is not None and category.kind != kind:
                    continue
                if kind is not None and line_kind != kind:
                    continue
                transaction_currency = line.currency
                if currency is not None and transaction_currency != currency:
                    continue
                group = groups.setdefault(
                    (category.category_id, transaction_currency),
                    {
                        "category_id": category.category_id,
                        "kind": category.kind,
                        "primary": category.primary,
                        "secondary": category.secondary,
                        "currency": transaction_currency,
                        "amount": Decimal("0"),
                        "transaction_count": 0,
                        "transaction_ids": [],
                    },
                )
                group["amount"] += line.amount
                group["transaction_count"] += 1
                group["transaction_ids"].append(transaction.transaction_id)

        return {
            "kind": kind,
            "currency": currency,
            "groups": [
                {
                    "category_id": group["category_id"],
                    "kind": group["kind"],
                    "primary": group["primary"],
                    "secondary": group["secondary"],
                    "currency": group["currency"],
                    "amount": str(group["amount"]),
                    "transaction_count": group["transaction_count"],
                    "transaction_ids": sorted(group["transaction_ids"]),
                }
                for group in sorted(
                    groups.values(),
                    key=lambda item: (
                        item["kind"],
                        item["primary"],
                        item["secondary"] or "",
                        item["currency"],
                    ),
                )
            ],
        }

    def _report_lines_for_transaction(self, transaction):
        return transaction.lines or []
