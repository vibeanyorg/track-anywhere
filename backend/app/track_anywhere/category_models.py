from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .books import DEFAULT_BOOK_ID, DEFAULT_OWNER_ID


@dataclass
class Category:
    category_id: str
    kind: str
    primary: str = ""
    secondary: str | None = None
    book_id: str = DEFAULT_BOOK_ID
    parent_id: str | None = None
    name: str = ""
    normalized_name: str = ""
    level: int = 1
    path_cache: str = ""
    icon: str | None = None
    color: str | None = None
    sort_order: int = 0
    status: str = "active"
    version: int = 1

    def __post_init__(self) -> None:
        self.name = self.name or self.secondary or self.primary
        if not self.primary:
            self.primary = self.name
        self.normalized_name = self.normalized_name or normalize_key(self.name)
        self.level = 2 if self.parent_id else 1
        self.path_cache = self.path_cache or self.primary if self.secondary is None else f"{self.primary} / {self.secondary}"


@dataclass
class CategoryAlias:
    alias_id: str
    book_id: str
    category_id: str
    alias: str
    normalized_alias: str
    locale: str = "zh-CN"
    source: str = "manual"
    confidence: float = 1.0
    status: str = "active"
    version: int = 1


@dataclass
class CategoryVersion:
    category_version_id: str
    category_id: str
    book_id: str
    name: str
    parent_id: str | None
    path: str
    icon: str | None = None
    color: str | None = None
    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_to: datetime | None = None
    change_reason: str = "create"
    version: int = 1


@dataclass
class ClassificationEvent:
    classification_event_id: str
    book_id: str
    event_type: str
    source_category_id: str | None = None
    target_category_id: str | None = None
    affected_line_count: int = 0
    before: dict[str, object] = field(default_factory=dict)
    after: dict[str, object] = field(default_factory=dict)
    rollback: dict[str, object] = field(default_factory=dict)
    created_by: str = DEFAULT_OWNER_ID
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = 1


def normalize_key(value: str) -> str:
    return " ".join(value.strip().split()).casefold()
