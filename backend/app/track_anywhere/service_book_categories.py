from __future__ import annotations

from typing import Any

from .commands import CreateCategoryCommand
from .domain_commands import AddCategoryAliasCommand, MergeCategoryCommand, UpdateCategoryCommand
from .errors import ValidationError


class BookCategoryUseCases:
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

        category, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="category.update", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, self._commit_catalog_change)
        return category, replay

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

        alias, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="category.alias.add", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, self._commit_catalog_change)
        return alias, replay

    def merge_book_category(self, token: str, book_id: str, category_id: str, payload: dict[str, Any], *, idempotency_key: str):
        actor = self.actor_from_token(token, "category:write")
        self.books.require_access(book_id, actor, "category:write")
        command = MergeCategoryCommand.model_validate(payload)
        request_hash = self._hash_command_payload(command, {"book_id": book_id, "category_id": category_id})

        def run():
            affected = sum(
                1
                for transaction in self._list_all_transactions_from_storage(book_id=book_id)
                for line in transaction.lines
                if line.category_id == category_id
            )
            source = self.categories.merge(category_id, command.target_category_id, affected_line_count=affected, actor_id=actor.actor_id)
            self.audit.record(operation="category.merge", actor=actor, entity_ref=category_id, details={"target_category_id": command.target_category_id, "affected_line_count": affected})
            return source

        category, replay = self.idempotency.run(key=idempotency_key, actor=actor, operation="category.merge", request_hash=request_hash, fn=run)
        self._commit_replay_or(replay, self._commit_catalog_change)
        return category, replay

    def list_book_classification_events(self, token: str, book_id: str) -> list[Any]:
        self.actor_from_token(token, "category:read")
        self.actor_for_book(token, book_id, "category:read")
        return sorted(
            [event for event in self.categories.events.values() if event.book_id == book_id],
            key=lambda event: (event.created_at, event.classification_event_id),
        )
