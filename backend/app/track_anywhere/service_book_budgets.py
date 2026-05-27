from __future__ import annotations

from typing import Any

from .books import DEFAULT_BOOK_ID
from .domain_commands import CreateBudgetCommand, CreateBudgetTargetCommand
from .errors import ValidationError


class BookBudgetUseCases:
    def create_budget(self, token: str, book_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "budget:write")
        self.books.require_access(book_id, actor, "budget:write")
        command = CreateBudgetCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id})

        def run():
            budget = self.budgets.create_budget(book_id=book_id, **command.model_dump(exclude={"schema_version"}))
            self.audit.record(operation="budget.create", actor=actor, entity_ref=budget.budget_id, details=command.model_dump(mode="json"))
            return budget

        budget, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="budget.create", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, lambda: self._commit_finance_change(budgets=True))
        return budget, replay

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

        target, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="budget.target.add", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, lambda: self._commit_finance_change(budgets=True))
        return target, replay

    def list_budgets(self, token: str, book_id: str = DEFAULT_BOOK_ID) -> list[Any]:
        self.actor_for_book(token, book_id, "budget:read")
        return self.budgets.list_budgets(book_id=book_id)

    def list_budget_targets(self, token: str, book_id: str, budget_id: str) -> list[Any]:
        self.actor_for_book(token, book_id, "budget:read")
        budget = self.budgets.get_budget(budget_id)
        if budget.book_id != book_id:
            raise ValidationError("budget does not belong to book")
        return self.budgets.list_targets(budget_id)
