from __future__ import annotations

from decimal import Decimal
from typing import Any

from .books import DEFAULT_BOOK_ID, LedgerBook
from .commands import CreateAccountCommand, CreateCategoryCommand, RecordTransactionCommand, ReverseTransactionCommand
from .domain_commands import (
    AddCategoryAliasCommand,
    CreateBookCommand,
    CreateBudgetCommand,
    CreateBudgetTargetCommand,
    MergeCategoryCommand,
    UpdateCategoryCommand,
)
from .errors import ValidationError


class BookUseCases:
    def list_books(self, token: str) -> list[LedgerBook]:
        actor = self.actor_from_token(token, "book:read")
        if actor.actor_id == "owner":
            return self.books.list()
        member_book_ids = {book_id for book_id, user_id in self.books.members if user_id == actor.actor_id}
        return [book for book in self.books.list() if book.book_id in member_book_ids]

    def get_book(self, token: str, book_id: str) -> LedgerBook:
        actor = self.actor_for_book(token, book_id, "book:read")
        return self.books.require_access(book_id, actor, "book:read")

    def create_book(self, token: str, payload: dict[str, Any], *, idempotency_key: str) -> tuple[LedgerBook, bool]:
        actor = self.actor_from_token(token, "book:write")
        command = CreateBookCommand.model_validate(payload)
        request_hash = self._hash_command(command)

        def run():
            book = self.books.create(
                name=command.name,
                kind=command.kind,
                base_currency=command.base_currency,
                timezone=command.timezone,
                template_key=command.template_key,
                created_by=actor.actor_id,
            )
            self.audit.record(operation="book.create", actor=actor, entity_ref=book.book_id, details=command.model_dump(mode="json"))
            return book

        result = self.idempotency.run(key=idempotency_key, actor=actor, operation="book.create", request_hash=request_hash, fn=run)
        self._persist()
        return result

    def list_book_accounts(self, token: str, book_id: str) -> list[Any]:
        return self.list_accounts(token, book_id=book_id)

    def create_book_account(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "account:write")
        CreateAccountCommand.model_validate({**payload, "book_id": book_id})
        return self.create_account(token, {**payload, "book_id": book_id}, idempotency_key=idempotency_key)

    def list_book_transactions(self, token: str, book_id: str, *, limit: int = 20) -> list[Any]:
        self.actor_for_book(token, book_id, "ledger:read")
        transactions = [transaction for transaction in self.ledger.transactions.values() if transaction.book_id == book_id]
        transactions.sort(key=lambda transaction: (transaction.occurred_at, transaction.transaction_id), reverse=True)
        return transactions[: max(0, min(limit, 200))]

    def record_book_transaction(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "ledger:confirm")
        command = RecordTransactionCommand.model_validate(payload)
        for account_id in (command.from_account_id, command.to_account_id):
            if self.ledger.get_account(account_id).book_id != book_id:
                raise ValidationError("book transaction accounts must belong to the route book")
        return self.record_transaction(token, payload, idempotency_key=idempotency_key)

    def reverse_book_transaction(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "ledger:reverse")
        command = ReverseTransactionCommand.model_validate(payload)
        transaction = self.get_transaction(token, command.transaction_id)
        if transaction.book_id != book_id:
            raise ValidationError("transaction does not belong to book")
        return self.reverse_transaction(token, payload, idempotency_key=idempotency_key)

    def create_book_category(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        self.actor_for_book(token, book_id, "category:write")
        command = CreateCategoryCommand.model_validate(payload)
        return self._create_category(token, command, idempotency_key=idempotency_key, book_id=book_id)

    def list_book_categories(
        self,
        token: str,
        book_id: str,
        *,
        kind: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> list[Any]:
        self.actor_for_book(token, book_id, "category:read")
        return self.list_categories(token, kind=kind, name=name, parent_id=parent_id, book_id=book_id)

    def update_book_category(self, token: str, book_id: str, category_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "category:write")
        self.books.require_access(book_id, actor, "category:write")
        command = UpdateCategoryCommand.model_validate(payload)
        if not command.model_dump(exclude_none=True):
            raise ValidationError("at least one category field is required")
        request_hash = self._hash_command_payload(command, {"book_id": book_id, "category_id": category_id})

        def run():
            category = self.categories.get(category_id)
            if category.book_id != book_id:
                raise ValidationError("category does not belong to book")
            if command.name is not None:
                category = self.categories.rename(category_id, name=command.name, actor_id=actor.actor_id)
            if command.parent_id is not None:
                category = self.categories.move(category_id, parent_id=command.parent_id, actor_id=actor.actor_id)
            for field_name in ("icon", "color", "sort_order", "status"):
                value = getattr(command, field_name)
                if value is not None:
                    setattr(category, field_name, value)
                    category.version += 1
            self.audit.record(operation="category.update", actor=actor, entity_ref=category_id, details=command.model_dump(mode="json", exclude_none=True))
            return category

        result = self.idempotency.run(key=idempotency_key, actor=actor, operation="category.update", request_hash=request_hash, fn=run)
        self._persist()
        return result

    def add_book_category_alias(self, token: str, book_id: str, category_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "category:write")
        self.books.require_access(book_id, actor, "category:write")
        command = AddCategoryAliasCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id, "category_id": category_id})

        def run():
            category = self.categories.get(category_id)
            if category.book_id != book_id:
                raise ValidationError("category does not belong to book")
            alias = self.categories.add_alias(category_id, alias=command.alias, source=command.source, actor_id=actor.actor_id)
            self.audit.record(operation="category.alias.add", actor=actor, entity_ref=category_id, details=command.model_dump(mode="json"))
            return alias

        result = self.idempotency.run(key=idempotency_key, actor=actor, operation="category.alias.add", request_hash=request_hash, fn=run)
        self._persist()
        return result

    def merge_book_category(self, token: str, book_id: str, category_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "category:write")
        self.books.require_access(book_id, actor, "category:write")
        command = MergeCategoryCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id, "category_id": category_id})

        def run():
            affected = sum(1 for transaction in self.ledger.transactions.values() for line in transaction.lines if line.category_id == category_id)
            source = self.categories.merge(category_id, command.target_category_id, affected_line_count=affected, actor_id=actor.actor_id)
            self.audit.record(operation="category.merge", actor=actor, entity_ref=category_id, details={"target_category_id": command.target_category_id, "affected_line_count": affected})
            return source

        result = self.idempotency.run(key=idempotency_key, actor=actor, operation="category.merge", request_hash=request_hash, fn=run)
        self._persist()
        return result

    def list_book_classification_events(self, token: str, book_id: str) -> list[Any]:
        self.actor_from_token(token, "category:read")
        self.actor_for_book(token, book_id, "category:read")
        return sorted(
            [event for event in self.categories.events.values() if event.book_id == book_id],
            key=lambda event: (event.created_at, event.classification_event_id),
        )

    def create_budget(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "budget:write")
        self.books.require_access(book_id, actor, "budget:write")
        command = CreateBudgetCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id})

        def run():
            budget = self.budgets.create_budget(book_id=book_id, **command.model_dump(exclude={"schema_version"}))
            self.audit.record(operation="budget.create", actor=actor, entity_ref=budget.budget_id, details=command.model_dump(mode="json"))
            return budget

        result = self.idempotency.run(key=idempotency_key, actor=actor, operation="budget.create", request_hash=request_hash, fn=run)
        self._persist()
        return result

    def add_budget_target(self, token: str, book_id: str, budget_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "budget:write")
        budget = self.budgets.get_budget(budget_id)
        self.books.require_access(book_id, actor, "budget:write")
        if budget.book_id != book_id:
            raise ValidationError("budget does not belong to book")
        command = CreateBudgetTargetCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id, "budget_id": budget_id})

        def run():
            target = self.budgets.add_target(budget_id=budget_id, **command.model_dump(exclude={"schema_version"}))
            self.audit.record(operation="budget.target.add", actor=actor, entity_ref=budget_id, details=command.model_dump(mode="json"))
            return target

        result = self.idempotency.run(key=idempotency_key, actor=actor, operation="budget.target.add", request_hash=request_hash, fn=run)
        self._persist()
        return result

    def list_budgets(self, token: str, book_id: str = DEFAULT_BOOK_ID) -> list[Any]:
        self.actor_for_book(token, book_id, "budget:read")
        return self.budgets.list_budgets(book_id=book_id)

    def list_budget_targets(self, token: str, book_id: str, budget_id: str) -> list[Any]:
        self.actor_for_book(token, book_id, "budget:read")
        budget = self.budgets.get_budget(budget_id)
        if budget.book_id != book_id:
            raise ValidationError("budget does not belong to book")
        return self.budgets.list_targets(budget_id)

    def budget_execution_report(self, token: str, book_id: str, budget_id: str) -> dict[str, Any]:
        self.actor_for_book(token, book_id, "budget:read")
        budget = self.budgets.get_budget(budget_id)
        if budget.book_id != book_id:
            raise ValidationError("budget does not belong to book")
        target_reports = []
        total_spent = Decimal("0")
        for target in self.budgets.list_targets(budget_id):
            amount = self._budget_target_spend(book_id, target)
            if target.mode == "exclude":
                total_spent -= amount
            else:
                total_spent += amount
            target_reports.append(
                {
                    "budget_target_id": target.budget_target_id,
                    "target_type": target.target_type,
                    "target_id": target.target_id,
                    "mode": target.mode,
                    "amount": str(amount),
                }
            )
        return {
            "book_id": book_id,
            "budget_id": budget_id,
            "currency": budget.currency,
            "total_amount": str(budget.total_amount),
            "spent": str(total_spent),
            "remaining": str(budget.total_amount - total_spent),
            "targets": target_reports,
        }

    def spending_report(self, token: str, book_id: str, *, group_by: str = "category_parent", currency: str | None = None) -> dict[str, Any]:
        self.actor_for_book(token, book_id, "ledger:read")
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for transaction in self.ledger.transactions.values():
            if transaction.book_id != book_id or transaction.reversed_by is not None:
                continue
            for line in self._report_lines_for_transaction(transaction):
                if currency is not None and line.currency != currency:
                    continue
                key = self._spending_report_key(line, group_by)
                group = groups.setdefault((key, line.currency), {"key": key, "currency": line.currency, "amount": Decimal("0"), "line_count": 0})
                group["amount"] += line.amount
                group["line_count"] += 1
        return {"book_id": book_id, "group_by": group_by, "currency": currency, "groups": [{"key": item["key"], "currency": item["currency"], "amount": str(item["amount"]), "line_count": item["line_count"]} for item in sorted(groups.values(), key=lambda item: (item["currency"], item["key"]))]}

    def _spending_report_key(self, line, group_by: str) -> str:
        if group_by == "category":
            return line.category_id or "uncategorized"
        if group_by == "category_parent":
            snapshot = line.category_path_snapshot or {}
            return str(snapshot.get("primary") or "uncategorized")
        if group_by == "necessity":
            return line.necessity
        raise ValidationError("unsupported spending report grouping")

    def _budget_target_spend(self, book_id: str, target) -> Decimal:
        total = Decimal("0")
        for transaction in self.ledger.transactions.values():
            if transaction.book_id != book_id or transaction.reversed_by is not None:
                continue
            for line in self._report_lines_for_transaction(transaction):
                if self._line_matches_budget_target(line, target):
                    total += line.amount
        return total

    def _line_matches_budget_target(self, line, target) -> bool:
        if target.target_type == "book":
            return True
        if target.target_type == "category_node":
            return line.category_id == target.target_id
        if target.target_type == "category_subtree":
            if line.category_id == target.target_id:
                return True
            category = self.categories.categories.get(line.category_id)
            return bool(category and category.parent_id == target.target_id)
        if target.target_type == "project":
            return line.project_id == target.target_id
        if target.target_type == "merchant":
            return line.merchant_id == target.target_id
        return False
