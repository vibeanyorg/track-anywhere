from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .contracts import CategoryRef, ClarificationChoice
from .errors import EntryErrorCode, EntryGatewayError


class CategoryUsageKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class EntryCategory:
    category_id: UUID
    category_version_id: UUID
    book_id: UUID
    path: tuple[str, ...]
    usage_kind: CategoryUsageKind
    status: str = "active"
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.path or any(not part.strip() for part in self.path):
            raise ValueError("category path must be non-empty")


@dataclass(frozen=True, slots=True)
class CategoryResolution:
    category: EntryCategory | None
    choices: tuple[ClarificationChoice, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return self.category is None and bool(self.choices)


def resolve_category(
    reference: CategoryRef,
    *,
    categories: tuple[EntryCategory, ...],
    book_id: UUID,
    usage_kind: CategoryUsageKind,
    account_ids: frozenset[UUID] = frozenset(),
) -> CategoryResolution:
    if reference.category_id is not None:
        if reference.category_id in account_ids:
            raise EntryGatewayError(
                EntryErrorCode.CATEGORY_INELIGIBLE,
                "an account ID cannot be used as a category",
                field="category",
            )
        matching_id = tuple(
            category
            for category in categories
            if category.book_id == book_id
            and category.category_id == reference.category_id
        )
        if not matching_id:
            raise EntryGatewayError(
                EntryErrorCode.CATEGORY_NOT_FOUND,
                "category was not found in the requested Book",
                field="category",
            )
        category = matching_id[0]
        _require_eligible(category, usage_kind=usage_kind)
        return CategoryResolution(category=category)

    eligible = tuple(
        category
        for category in categories
        if category.book_id == book_id
        and category.status == "active"
        and category.usage_kind in {usage_kind, CategoryUsageKind.BOTH}
    )
    if reference.path is not None:
        normalized_path = tuple(_normalize(part) for part in reference.path)
        candidates = tuple(
            category
            for category in eligible
            if tuple(_normalize(part) for part in category.path) == normalized_path
        )
    else:
        assert reference.query is not None
        query = _normalize(reference.query)
        candidates = tuple(
            category
            for category in eligible
            if query == _normalize(category.path[-1])
            or query in {_normalize(alias) for alias in category.aliases}
        )

    if not candidates:
        raise EntryGatewayError(
            EntryErrorCode.CATEGORY_NOT_FOUND,
            "no eligible category exactly matches the reference",
            field="category",
        )
    if len(candidates) > 1:
        return CategoryResolution(
            category=None,
            choices=tuple(
                ClarificationChoice(
                    choice_id=str(category.category_id),
                    label=" / ".join(category.path),
                    resolved_id=category.category_id,
                )
                for category in sorted(candidates, key=lambda item: str(item.category_id))
            ),
        )
    return CategoryResolution(category=candidates[0])


def _require_eligible(
    category: EntryCategory,
    *,
    usage_kind: CategoryUsageKind,
) -> None:
    if (
        category.status != "active"
        or category.usage_kind not in {usage_kind, CategoryUsageKind.BOTH}
    ):
        raise EntryGatewayError(
            EntryErrorCode.CATEGORY_INELIGIBLE,
            "selected category is not eligible for this entry",
            field="category",
        )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


__all__ = [
    "CategoryResolution",
    "CategoryUsageKind",
    "EntryCategory",
    "resolve_category",
]
