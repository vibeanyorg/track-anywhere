from __future__ import annotations

from decimal import Decimal
from typing import Any

from .categories import Category
from .commands import CreateCategoryCommand
from .errors import ValidationError


class CategoryUseCases:
    def category_summary(
        self,
        token: str,
        *,
        kind: str | None = None,
        currency: str | None = None,
    ) -> dict[str, Any]:
        self.actor_from_token(token, "ledger:read")
        if kind is not None and kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")

        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for transaction in self.ledger.transactions.values():
            if transaction.reversed_by is not None or transaction.category_id is None:
                continue
            category = self.categories.get(transaction.category_id)
            if kind is not None and category.kind != kind:
                continue
            amounts = self._category_amounts_for_transaction(transaction, category)
            for transaction_currency, amount in amounts.items():
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
                group["amount"] += amount
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

    def create_category(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Category, bool]:
        actor = self.actor_from_token(token, "category:write")
        command = CreateCategoryCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            category = self.categories.create(
                kind=command.kind,
                primary=command.primary,
                secondary=command.secondary,
            )
            self.audit.record(
                operation="category.create",
                actor=actor,
                entity_ref=category.category_id,
                details=command.model_dump(mode="json", exclude_none=True),
            )
            return category

        result = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="category.create",
            request_hash=request_hash,
            fn=run,
        )
        self._persist()
        return result

    def list_categories(
        self,
        token: str,
        *,
        kind: str | None = None,
        primary: str | None = None,
        secondary: str | None = None,
    ) -> list[Category]:
        self.actor_from_token(token, "category:read")
        if kind is not None and kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")
        return self.categories.list(kind=kind, primary=primary, secondary=secondary)

    def get_category(self, token: str, category_id: str) -> Category:
        self.actor_from_token(token, "category:read")
        return self.categories.get(category_id)

    def _category_amounts_for_transaction(self, transaction, category: Category) -> dict[str, Decimal]:
        amounts: dict[str, Decimal] = {}
        for posting in transaction.postings:
            account = self.ledger.accounts.get(posting.account_id)
            if account is None:
                continue
            if category.kind == "expense" and account.type == "expense" and posting.amount > Decimal("0"):
                amounts[posting.currency] = amounts.get(posting.currency, Decimal("0")) + posting.amount
            elif category.kind == "income" and account.type == "income" and posting.amount < Decimal("0"):
                amounts[posting.currency] = amounts.get(posting.currency, Decimal("0")) - posting.amount
        return amounts
