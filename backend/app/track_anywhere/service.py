from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .assets import AssetCatalog
from .audit import AuditLog
from .attachments import AttachmentIntake
from .auth_identities import AuthIdentityDirectory
from .books import BookDirectory
from .budgets import BudgetBook
from .categories import CategoryBook
from .counterparties import CounterpartyDirectory
from .credit_cards import CreditCardBook
from .drafts import DraftBook
from .idempotency import IdempotencyStore
from .investments import InvestmentBook
from .ledger import Ledger
from .recurring import RecurringBook
from .security import Actor, CredentialReference, CredentialStore, DeploymentSecurityConfig, validate_startup_security
from .service_assets import AssetUseCases
from .service_backoffice import BackofficeUseCases
from .service_balances import BalanceUseCases
from .service_books import BookUseCases
from .service_catalog import CatalogUseCases
from .service_credentials import CredentialUseCases
from .service_drafts import DraftUseCases
from .service_finance import FinancialUseCases
from .service_fx import FxUseCases
from .service_foundations import DomainFoundationBootstrap
from .service_identity import IdentityUseCases
from .service_investments import InvestmentUseCases
from .service_ledger import LedgerUseCases
from .service_persistence import ServicePersistenceMixin
from .service_platform_auth import PlatformAuthUseCases
from .service_owner_bootstrap import OwnerCredentialBootstrap
from .service_reclassification import ReclassificationUseCases
from .service_reports import BookReportUseCases
from .service_recurring import RecurringUseCases
from .service_state_hydration import ServiceStateHydration
from .storage import OrmStorage, new_owner_token
from .storage_json import to_jsonable
from .payment_instruments import PaymentInstrumentDirectory
from .payment_profiles import PaymentProfileDirectory
from .users import UserDirectory


class FinanceService(
    ServiceStateHydration,
    OwnerCredentialBootstrap,
    DomainFoundationBootstrap,
    ServicePersistenceMixin,
    PlatformAuthUseCases,
    BackofficeUseCases,
    IdentityUseCases,
    BookUseCases,
    BookReportUseCases,
    CatalogUseCases,
    AssetUseCases,
    InvestmentUseCases,
    FinancialUseCases,
    CredentialUseCases,
    DraftUseCases,
    RecurringUseCases,
    BalanceUseCases,
    FxUseCases,
    ReclassificationUseCases,
    LedgerUseCases,
):
    def __init__(
        self,
        config: DeploymentSecurityConfig | None = None,
        *,
        database_url: str | None = None,
        persist_on_initialize: bool = False,
    ) -> None:
        self.config = config or DeploymentSecurityConfig()
        self.startup_warnings = validate_startup_security(self.config)
        self.storage = OrmStorage(database_url)
        self.credentials = CredentialStore()
        self.audit = AuditLog()
        self.idempotency = IdempotencyStore()
        self.assets = AssetCatalog()
        self.books = BookDirectory()
        self.ledger = Ledger(asset_scale_lookup=self.assets.scale_for)
        self.payment_instruments = PaymentInstrumentDirectory()
        self.payment_profiles = PaymentProfileDirectory()
        self.counterparties = CounterpartyDirectory()
        self.drafts = DraftBook()
        self.recurring = RecurringBook()
        self.budgets = BudgetBook()
        self.investments = InvestmentBook()
        self.categories = CategoryBook()
        self.credit_cards = CreditCardBook()
        self.attachments = AttachmentIntake(self.config)
        self.users = UserDirectory()
        self.auth_identities = AuthIdentityDirectory()
        self.reconciliation_actions: list[dict[str, Any]] = []
        self.adjustment_account_ids: dict[str, str] = {}
        self.owner_token = new_owner_token()
        self._startup_persist_required = False
        self._apply_storage_snapshot(self.storage.load_snapshot())
        self._ensure_domain_foundations()
        self._ensure_owner_credential()
        if persist_on_initialize or self._startup_persist_required:
            self._commit_startup_maintenance()
        self.storage.refresh_read_cache_from_storage()

    def actor_from_token(self, token: str | CredentialReference, required_scope: str | None = None) -> Actor:
        return self.credentials.verify(token, required_scope=required_scope)

    def actor_for_book(self, token: str, book_id: str | None, required_scope: str | None = None) -> Actor:
        actor = self.actor_from_token(token, required_scope=required_scope)
        self.books.require_access(book_id, actor, required_scope)
        return actor

    @staticmethod
    def _hash_request_payload(
        operation: str,
        payload: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Return a stable idempotency hash for the client supplied request.

        Mutation commands may contain server-side dynamic defaults such as
        ``occurred_at=datetime.now(...)``.  Hashing the validated command would
        make two identical retries look different whenever the client omitted
        that field.  The idempotency boundary must therefore be the canonical
        raw request payload plus immutable route/context fields.
        """

        envelope: dict[str, Any] = {"operation": operation, "payload": payload or {}}
        if extra:
            envelope["extra"] = extra
        encoded = json.dumps(
            to_jsonable(envelope),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_command(command) -> str:
        return FinanceService._hash_request_payload(
            command.__class__.__name__,
            command.model_dump(mode="python", exclude_unset=True),
        )

    @staticmethod
    def _hash_command_payload(command, extra: dict[str, Any]) -> str:
        return FinanceService._hash_request_payload(
            command.__class__.__name__,
            command.model_dump(mode="python", exclude_unset=True),
            extra,
        )
