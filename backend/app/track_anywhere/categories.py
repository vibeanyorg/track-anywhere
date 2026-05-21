from __future__ import annotations

from uuid import uuid4

from .books import DEFAULT_BOOK_ID, DEFAULT_OWNER_ID
from .category_history import CategoryHistoryMixin
from .category_models import Category, CategoryAlias, CategoryVersion, ClassificationEvent, normalize_key
from .errors import NotFound, ValidationError


class CategoryBook(CategoryHistoryMixin):
    def __init__(self) -> None:
        self.categories: dict[str, Category] = {}
        self.aliases: dict[str, CategoryAlias] = {}
        self.versions: dict[str, CategoryVersion] = {}
        self.events: dict[str, ClassificationEvent] = {}

    def create(
        self,
        *,
        kind: str,
        name: str,
        parent_id: str | None = None,
        book_id: str = DEFAULT_BOOK_ID,
    ) -> Category:
        name = _normalize_label(name, "name")
        self._validate_kind(kind)
        parent = self.get(parent_id) if parent_id is not None else None
        if parent is not None:
            if parent.book_id != book_id or parent.kind != kind:
                raise ValidationError("category parent must belong to the same book and kind")
            if parent.level != 1:
                raise ValidationError("category parent must be a first-level category")
        existing = self._find_node(book_id=book_id, kind=kind, parent_id=parent_id, name=name)
        if existing is not None and existing.status == "active":
            raise ValidationError("category already exists")
        return self._create_node(book_id=book_id, kind=kind, name=name, parent=parent)

    def get(self, category_id: str) -> Category:
        try:
            return self.categories[category_id]
        except KeyError as exc:
            raise NotFound(f"category not found: {category_id}") from exc

    def list(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        book_id: str | None = None,
        status: str | None = "active",
    ) -> list[Category]:
        categories = list(self.categories.values())
        if book_id is not None:
            categories = [category for category in categories if category.book_id == book_id]
        if status is not None:
            categories = [category for category in categories if category.status == status]
        if kind is not None:
            categories = [category for category in categories if category.kind == kind]
        if name is not None:
            normalized_name = normalize_key(name)
            categories = [category for category in categories if category.normalized_name == normalized_name]
        if parent_id is not None:
            categories = [category for category in categories if category.parent_id == parent_id]
        return sorted(
            categories,
            key=lambda category: (
                category.book_id,
                category.kind,
                category.primary,
                category.level,
                category.secondary or "",
                category.sort_order,
                category.category_id,
            ),
        )

    def rename(self, category_id: str, *, name: str, actor_id: str = DEFAULT_OWNER_ID) -> Category:
        category = self.get(category_id)
        name = _normalize_label(name, "name")
        duplicate = self._find_node(
            book_id=category.book_id,
            kind=category.kind,
            parent_id=category.parent_id,
            name=name,
        )
        if duplicate is not None and duplicate.category_id != category.category_id and duplicate.status == "active":
            raise ValidationError("category already exists")
        before = self._snapshot(category)
        self._set_node_name(category, name)
        self._record_version(category, "rename")
        for child in self._children(category.category_id):
            self._sync_display_fields(child)
            self._record_version(child, "parent_rename")
        self._record_event("rename", category.book_id, category.category_id, before=before, after=self._snapshot(category), actor_id=actor_id)
        return category

    def move(self, category_id: str, *, parent_id: str, actor_id: str = DEFAULT_OWNER_ID) -> Category:
        category = self.get(category_id)
        parent = self.get(parent_id)
        if category.level != 2 or parent.level != 1 or category.category_id == parent.category_id:
            raise ValidationError("only second-level categories can move under a first-level category")
        if category.book_id != parent.book_id or category.kind != parent.kind:
            raise ValidationError("category move must stay within one book and kind")
        duplicate = self._find_node(book_id=category.book_id, kind=category.kind, parent_id=parent.category_id, name=category.name)
        if duplicate is not None and duplicate.category_id != category.category_id and duplicate.status == "active":
            raise ValidationError("category already exists under target parent")
        before = self._snapshot(category)
        category.parent_id = parent.category_id
        self._sync_display_fields(category)
        category.version += 1
        self._record_version(category, "move")
        self._record_event("move", category.book_id, category.category_id, target_id=parent.category_id, before=before, after=self._snapshot(category), actor_id=actor_id)
        return category

    def archive(self, category_id: str, *, actor_id: str = DEFAULT_OWNER_ID) -> Category:
        category = self.get(category_id)
        before = self._snapshot(category)
        category.status = "archived"
        category.version += 1
        self._record_version(category, "archive")
        self._record_event("archive", category.book_id, category.category_id, before=before, after=self._snapshot(category), actor_id=actor_id)
        return category

    def add_alias(self, category_id: str, *, alias: str, source: str = "manual", actor_id: str = DEFAULT_OWNER_ID) -> CategoryAlias:
        category = self.get(category_id)
        alias = _normalize_label(alias, "alias")
        normalized_alias = normalize_key(alias)
        for existing in self.aliases.values():
            if existing.book_id == category.book_id and existing.normalized_alias == normalized_alias and existing.status == "active":
                if existing.category_id == category.category_id:
                    raise ValidationError("category alias already exists")
                raise ValidationError("category alias conflicts with another category")
        category_alias = CategoryAlias(
            alias_id=f"alias_{uuid4().hex}",
            book_id=category.book_id,
            category_id=category.category_id,
            alias=alias,
            normalized_alias=normalized_alias,
            source=source,
        )
        self.aliases[category_alias.alias_id] = category_alias
        self._record_event("alias_add", category.book_id, category.category_id, after={"alias": alias}, actor_id=actor_id)
        return category_alias

    def merge(self, source_id: str, target_id: str, *, affected_line_count: int = 0, actor_id: str = DEFAULT_OWNER_ID) -> Category:
        source = self.get(source_id)
        target = self.get(target_id)
        if source.book_id != target.book_id or source.kind != target.kind:
            raise ValidationError("category merge must stay within one book and kind")
        before = self._snapshot(source)
        source.status = "archived"
        source.version += 1
        self._record_version(source, "merge")
        if source.name != target.name:
            alias_id = f"alias_{uuid4().hex}"
            self.aliases[alias_id] = CategoryAlias(
                alias_id=alias_id,
                book_id=source.book_id,
                category_id=target.category_id,
                alias=source.name,
                normalized_alias=normalize_key(source.name),
                source="merge",
            )
        self._record_event(
            "merge",
            source.book_id,
            source.category_id,
            target_id=target.category_id,
            affected_line_count=affected_line_count,
            before=before,
            after=self._snapshot(source),
            actor_id=actor_id,
        )
        return source

    def _create_node(self, *, book_id: str, kind: str, name: str, parent: Category | None) -> Category:
        category = Category(
            category_id=f"cat_{uuid4().hex}",
            kind=kind,
            primary=parent.name if parent else name,
            secondary=name if parent else None,
            book_id=book_id,
            parent_id=parent.category_id if parent else None,
            name=name,
        )
        self.categories[category.category_id] = category
        self._sync_display_fields(category)
        self._record_version(category, "create")
        self._record_event("create", category.book_id, category.category_id, after=self._snapshot(category))
        return category

    def _find_node(self, *, book_id: str, kind: str, parent_id: str | None, name: str) -> Category | None:
        normalized = normalize_key(name)
        for category in self.categories.values():
            if (
                category.book_id == book_id
                and category.kind == kind
                and category.parent_id == parent_id
                and category.normalized_name == normalized
                and category.status == "active"
            ):
                return category
        return None

    def _children(self, category_id: str) -> list[Category]:
        return [category for category in self.categories.values() if category.parent_id == category_id]

    def _set_node_name(self, category: Category, name: str) -> None:
        category.name = name
        category.normalized_name = normalize_key(name)
        category.version += 1
        self._sync_display_fields(category)

    def _sync_display_fields(self, category: Category) -> None:
        parent = self.categories.get(category.parent_id) if category.parent_id else None
        if parent is None:
            category.primary = category.name
            category.secondary = None
            category.level = 1
            category.path_cache = category.name
        else:
            category.primary = parent.name
            category.secondary = category.name
            category.level = 2
            category.path_cache = f"{parent.name} / {category.name}"

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")


def _normalize_label(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValidationError(f"{field_name} category label is required")
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValidationError(f"{field_name} category label must not be blank")
    return normalized
