from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StorageSnapshot:
    books: dict[str, Any]
    book_members: dict[tuple[str, str], Any]
    assets: dict[str, Any]
    users: dict[str, Any]
    auth_identities: dict[str, Any]
    drafts: dict[str, Any]
    recurring_items: dict[str, Any]
    budget_funds: dict[str, Any]
    budgets: dict[str, Any]
    budget_targets: dict[str, Any]
    counterparties: dict[str, Any]
    payment_profiles: dict[str, Any]
    payment_instruments: dict[str, Any]
    investment_events: dict[str, Any]
    investment_valuations: dict[str, Any]
    categories: dict[str, Any]
    category_aliases: dict[str, Any]
    category_versions: dict[str, Any]
    classification_events: dict[str, Any]
    credit_card_profiles: dict[str, Any]
    attachments: dict[str, Any]
    credentials: dict[str, Any]
    audit_events: list[Any]
    idempotency_receipts: dict[tuple[str, str, str], Any]
    reconciliation_actions: list[dict[str, Any]]
    adjustment_account_ids: dict[str, str]
    owner_token: str | None = None
