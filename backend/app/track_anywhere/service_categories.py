from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID
from .categories import Category
from .category_commands import EnsureCategoryPathCommand
from .commands import CreateCategoryCommand
from .errors import NotFound, ValidationError


class CategoryUseCases:
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
        for transaction in self.ledger.transactions.values():
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

    def create_category(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[Category, bool]:
        command = CreateCategoryCommand.model_validate(payload)
        return self._create_category(token, command, idempotency_key=idempotency_key)

    def _create_category(
        self,
        token: str,
        command: CreateCategoryCommand,
        *,
        idempotency_key: str,
        book_id: str | None = None,
    ) -> tuple[Category, bool]:
        target_book_id = book_id or self.books.ensure_default().book_id
        actor = self.actor_for_book(token, target_book_id, "category:write")
        request_hash = self._hash_command_payload(command, {"book_id": target_book_id})

        def run():
            category = self.categories.create(
                kind=command.kind,
                name=command.name,
                parent_id=command.parent_id,
                book_id=target_book_id,
            )
            self.audit.record(
                operation="category.create",
                actor=actor,
                entity_ref=category.category_id,
                details=command.model_dump(mode="json", exclude_none=True),
            )
            return category

        category, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="category.create",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_catalog_change()
        return category, replay

    def list_categories(
        self,
        token: str,
        *,
        kind: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        book_id: str | None = None,
    ) -> list[Category]:
        target_book_id = book_id or DEFAULT_BOOK_ID
        self.actor_for_book(token, target_book_id, "category:read")
        if kind is not None and kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")
        return self.storage.list_categories(kind=kind, name=name, parent_id=parent_id, book_id=target_book_id)

    def find_category_by_path(
        self,
        token: str,
        *,
        kind: str,
        path: str,
        book_id: str | None = None,
    ) -> Category:
        target_book_id = book_id or DEFAULT_BOOK_ID
        self.actor_for_book(token, target_book_id, "category:read")
        category = self.storage.find_category_by_path(book_id=target_book_id, kind=kind, path=path)
        if category is None:
            raise NotFound(f"category path not found: {path}")
        return category

    def ensure_category_path(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        command = EnsureCategoryPathCommand.model_validate(payload)
        target_book_id = self.books.ensure_default().book_id
        actor = self.actor_for_book(token, target_book_id, "category:write")
        request_hash = self._hash_command_payload(command, {"book_id": target_book_id})

        def run():
            parts = self.categories.split_path(command.path)
            created: list[Category] = []
            parent = self.categories.find_by_path(book_id=target_book_id, kind=command.kind, path=parts[0])
            if parent is None:
                parent = self.categories.create(kind=command.kind, name=parts[0], book_id=target_book_id)
                created.append(parent)
            category = parent
            if len(parts) == 2:
                child = self.categories.find_by_path(book_id=target_book_id, kind=command.kind, path=command.path)
                if child is None:
                    child = self.categories.create(
                        kind=command.kind,
                        name=parts[1],
                        parent_id=parent.category_id,
                        book_id=target_book_id,
                    )
                    created.append(child)
                category = child
            self.audit.record(
                operation="category.ensure_path",
                actor=actor,
                entity_ref=category.category_id,
                details={
                    "kind": command.kind,
                    "path": command.path,
                    "created_category_ids": [item.category_id for item in created],
                },
            )
            return {"category": category, "created_categories": created, "created": bool(created)}

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="category.ensure_path",
            request_hash=request_hash,
            fn=run,
        )
        if replay:
            self._persist_idempotency()
        else:
            self._persist_catalog_change()
        return result, replay

    def get_category(self, token: str, category_id: str) -> Category:
        category = self.storage.get_category(category_id)
        self.actor_for_book(token, category.book_id, "category:read")
        return category

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

    def _report_lines_for_transaction(self, transaction):
        return transaction.lines or []

    def _add_category_line_for_transaction(self, transaction, category: Category):
        amounts = self._category_amounts_for_transaction(transaction, category)
        for currency, amount in amounts.items():
            if amount > Decimal("0"):
                self.ledger.add_line(
                    transaction,
                    line_type=category.kind,
                    amount=amount,
                    currency=currency,
                    category_id=category.category_id,
                    category_version_id=self.categories.active_version(category.category_id).category_version_id,
                    category_path_snapshot=self.categories.path_snapshot(category.category_id),
                    memo=transaction.memo,
                )
