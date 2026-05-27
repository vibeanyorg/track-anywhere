from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
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


@dataclass(frozen=True)
class StartupMaintenanceChanges:
    book_changes: BookChanges = field(default_factory=BookChanges)
    assets: tuple[Any, ...] = ()
    categories: tuple[Any, ...] = ()
    category_history: CategoryHistoryChanges = field(default_factory=CategoryHistoryChanges)
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class IdempotencyChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class CredentialChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class AuditChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class AuthorizationGrantChanges:
    grants: tuple[Any, ...] = ()


@dataclass(frozen=True)
class DeviceGrantChanges:
    grants: tuple[Any, ...] = ()


@dataclass(frozen=True)
class CatalogChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)
    assets: tuple[Any, ...] = ()
    categories: tuple[Any, ...] = ()
    category_history: CategoryHistoryChanges = field(default_factory=CategoryHistoryChanges)
    counterparties: tuple[Any, ...] = ()
    payment_instruments: tuple[Any, ...] = ()


@dataclass(frozen=True)
class LedgerChanges:
    transactions: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)
    accounts: tuple[Any, ...] = ()
    assets: tuple[Any, ...] = ()
    adjustment_account_ids: dict[str, str] = field(default_factory=dict)
    category_history: CategoryHistoryChanges | None = None
    counterparties: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ReclassificationChanges:
    transaction: Any
    line_id: str
    category_history: CategoryHistoryChanges
    metadata: WriteMetadata


@dataclass(frozen=True)
class UserChanges:
    users: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class BookDirectoryChanges:
    book_changes: BookChanges = field(default_factory=BookChanges)
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class AuthLoginChanges:
    book_changes: BookChanges = field(default_factory=BookChanges)
    users: tuple[Any, ...] = ()
    identities: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class DraftChanges:
    drafts: tuple[Any, ...] = ()
    transactions: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class RecurringChanges:
    items: tuple[Any, ...] = ()
    drafts: tuple[Any, ...] = ()
    accounts: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class FinanceChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)
    funds: tuple[Any, ...] = ()
    budget_changes: BudgetChanges | None = None
    transactions: tuple[Any, ...] = ()
    accounts: tuple[Any, ...] = ()
    assets: tuple[Any, ...] = ()
    actions: tuple[Any, ...] = ()


@dataclass(frozen=True)
class InvestmentChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)
    events: tuple[Any, ...] = ()
    valuations: tuple[Any, ...] = ()
    transactions: tuple[Any, ...] = ()
    accounts: tuple[Any, ...] = ()
    assets: tuple[Any, ...] = ()
    adjustment_account_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CreditCardProfileChanges:
    profiles: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class PaymentProfileChanges:
    profiles: tuple[Any, ...] = ()
    metadata: WriteMetadata = field(default_factory=WriteMetadata)


@dataclass(frozen=True)
class AttachmentChanges:
    metadata: WriteMetadata = field(default_factory=WriteMetadata)
    attachments: tuple[Any, ...] = ()
    drafts: tuple[Any, ...] = ()


EMPTY_WRITE_METADATA = WriteMetadata()
