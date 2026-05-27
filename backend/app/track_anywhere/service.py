from __future__ import annotations

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
from .security import CredentialStore, DeploymentSecurityConfig, validate_startup_security
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
from .service_auth import ServiceAuthorization
from .service_idempotency import ServiceRequestHashing
from .service_identity import IdentityUseCases
from .service_investments import InvestmentUseCases
from .service_ledger import LedgerUseCases
from .service_persistence import ServicePersistenceMixin
from .service_platform_auth import PlatformAuthUseCases
from .service_password_auth import PasswordAuthUseCases
from .service_owner_bootstrap import OwnerCredentialBootstrap
from .service_reclassification import ReclassificationUseCases
from .service_reports import BookReportUseCases
from .service_recurring import RecurringUseCases
from .service_state_hydration import ServiceStateHydration
from .service_system import SystemStatusUseCases
from .storage import OrmStorage, new_owner_token
from .platform_auth import PlatformKeyExchange
from .payment_instruments import PaymentInstrumentDirectory
from .payment_profiles import PaymentProfileDirectory
from .users import UserDirectory


class FinanceService(
    ServiceStateHydration,
    OwnerCredentialBootstrap,
    DomainFoundationBootstrap,
    ServiceAuthorization,
    ServiceRequestHashing,
    ServicePersistenceMixin,
    PlatformAuthUseCases,
    PasswordAuthUseCases,
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
    SystemStatusUseCases,
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
        self.platform_key_exchange = PlatformKeyExchange()
        self.reconciliation_actions: list[dict[str, object]] = []
        self.adjustment_account_ids: dict[str, str] = {}
        self.owner_token = new_owner_token()
        self._startup_persist_required = False
        self._apply_storage_snapshot(self.storage.load_snapshot())
        self._ensure_domain_foundations()
        self._ensure_owner_credential()
        if persist_on_initialize or self._startup_persist_required:
            self._commit_startup_maintenance()
        self.storage.refresh_read_cache_from_storage()
