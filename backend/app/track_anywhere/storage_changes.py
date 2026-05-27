from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WriteMetadata:
    credentials: tuple[Any, ...] = ()
    audit_events: tuple[Any, ...] = ()
    idempotency_receipts: tuple[Any, ...] = ()


@dataclass(frozen=True)
class CategoryHistoryChanges:
    aliases: tuple[Any, ...] = ()
    versions: tuple[Any, ...] = ()
    events: tuple[Any, ...] = ()


@dataclass(frozen=True)
class BookChanges:
    books: tuple[Any, ...] = ()
    members: tuple[Any, ...] = ()


@dataclass(frozen=True)
class BudgetChanges:
    budgets: tuple[Any, ...] = ()
    targets: tuple[Any, ...] = ()


EMPTY_WRITE_METADATA = WriteMetadata()
