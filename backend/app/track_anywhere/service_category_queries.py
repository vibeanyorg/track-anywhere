from __future__ import annotations

from .books import DEFAULT_BOOK_ID
from .categories import Category
from .errors import NotFound, ValidationError


class CategoryQueryUseCases:
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
        return self._list_categories_from_storage(
            kind=kind,
            name=name,
            parent_id=parent_id,
            book_id=target_book_id,
        )

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
        category = self._find_category_by_path_from_storage(book_id=target_book_id, kind=kind, path=path)
        if category is None:
            raise NotFound(f"category path not found: {path}")
        return category

    def get_category(self, token: str, category_id: str) -> Category:
        category = self._get_category_from_storage(category_id)
        self.actor_for_book(token, category.book_id, "category:read")
        return category

    def _list_categories_from_storage(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        book_id: str | None = DEFAULT_BOOK_ID,
        status: str | None = "active",
    ) -> list[Category]:
        with self.storage.unit_of_work() as uow:
            return uow.categories.list_categories(
                kind=kind,
                name=name,
                parent_id=parent_id,
                book_id=book_id,
                status=status,
            )

    def _find_category_by_path_from_storage(self, *, book_id: str, kind: str, path: str) -> Category | None:
        with self.storage.unit_of_work() as uow:
            return uow.categories.find_category_by_path(book_id=book_id, kind=kind, path=path)

    def _get_category_from_storage(self, category_id: str) -> Category:
        with self.storage.unit_of_work() as uow:
            return uow.categories.get_category(category_id)
