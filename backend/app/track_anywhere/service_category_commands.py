from __future__ import annotations

from typing import Any

from .categories import Category
from .category_commands import EnsureCategoryPathCommand
from .commands import CreateCategoryCommand


class CategoryCommandUseCases:
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
        self._commit_replay_or(replay, self._commit_catalog_change)
        return category, replay

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
        self._commit_replay_or(replay, self._commit_catalog_change)
        return result, replay
