from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .errors import NotFound, ValidationError


@dataclass
class Category:
    category_id: str
    kind: str
    primary: str
    secondary: str | None = None
    version: int = 1


class CategoryBook:
    def __init__(self) -> None:
        self.categories: dict[str, Category] = {}

    def create(self, *, kind: str, primary: str, secondary: str | None = None) -> Category:
        primary = _normalize_label(primary, "primary")
        secondary = _normalize_label(secondary, "secondary") if secondary is not None else None
        if kind not in {"income", "expense"}:
            raise ValidationError("category kind must be income or expense")
        if self.find(kind=kind, primary=primary, secondary=secondary):
            raise ValidationError("category already exists")
        category = Category(
            category_id=f"cat_{uuid4().hex}",
            kind=kind,
            primary=primary,
            secondary=secondary,
        )
        self.categories[category.category_id] = category
        return category

    def get(self, category_id: str) -> Category:
        try:
            return self.categories[category_id]
        except KeyError as exc:
            raise NotFound(f"category not found: {category_id}") from exc

    def find(self, *, kind: str, primary: str, secondary: str | None = None) -> Category | None:
        for category in self.categories.values():
            if category.kind == kind and category.primary == primary and category.secondary == secondary:
                return category
        return None

    def list(
        self,
        *,
        kind: str | None = None,
        primary: str | None = None,
        secondary: str | None = None,
    ) -> list[Category]:
        categories = list(self.categories.values())
        if kind is not None:
            categories = [category for category in categories if category.kind == kind]
        if primary is not None:
            categories = [category for category in categories if category.primary == primary]
        if secondary is not None:
            categories = [category for category in categories if category.secondary == secondary]
        return sorted(
            categories,
            key=lambda category: (
                category.kind,
                category.primary,
                category.secondary or "",
                category.category_id,
            ),
        )


def _normalize_label(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValidationError(f"{field_name} category label is required")
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValidationError(f"{field_name} category label must not be blank")
    return normalized
