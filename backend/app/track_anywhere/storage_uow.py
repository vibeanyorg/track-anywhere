from __future__ import annotations

from .storage_repositories import (
    AssetRepository,
    AttachmentRepository,
    AccountRepository,
    AuditRepository,
    AuthIdentityRepository,
    BookRepository,
    BudgetRepository,
    CategoryRepository,
    CredentialRepository,
    CounterpartyRepository,
    CreditCardRepository,
    DraftRepository,
    FundRepository,
    IdempotencyRepository,
    InvestmentRepository,
    LedgerRepository,
    PaymentInstrumentRepository,
    PaymentProfileRepository,
    PlatformGrantRepository,
    ReconciliationRepository,
    RecurringRepository,
    StateRepository,
    TransactionRepository,
    UserRepository,
)
from .storage_password_accounts import StoragePasswordAccountRepository


class StorageUnitOfWork:
    def __init__(self, storage) -> None:
        self.storage = storage
        self._context = None
        self.session = None

    def __enter__(self):
        self._context = self.storage.session_factory.begin()
        self.session = self._context.__enter__()
        self.accounts = AccountRepository(self.storage, self.session)
        self.transactions = TransactionRepository(self.session)
        self.ledger = LedgerRepository(self.storage, self.session)
        self.state = StateRepository(self.session)
        self.assets = AssetRepository(self.storage, self.session)
        self.books = BookRepository(self.storage, self.session)
        self.users = UserRepository(self.session)
        self.identities = AuthIdentityRepository(self.session)
        self.categories = CategoryRepository(self.storage, self.session)
        self.counterparties = CounterpartyRepository(self.storage, self.session)
        self.payment_instruments = PaymentInstrumentRepository(self.storage, self.session)
        self.payment_profiles = PaymentProfileRepository(self.storage, self.session)
        self.credit_cards = CreditCardRepository(self.session)
        self.drafts = DraftRepository(self.storage, self.session)
        self.recurring = RecurringRepository(self.storage, self.session)
        self.funds = FundRepository(self.storage, self.session)
        self.budgets = BudgetRepository(self.storage, self.session)
        self.investments = InvestmentRepository(self.storage, self.session)
        self.attachments = AttachmentRepository(self.session)
        self.reconciliation = ReconciliationRepository(self.session)
        self.audit = AuditRepository(self.storage, self.session)
        self.credentials = CredentialRepository(self.storage, self.session)
        self.idempotency = IdempotencyRepository(self.storage, self.session)
        self.platform_grants = PlatformGrantRepository(self.session)
        self.password_accounts = StoragePasswordAccountRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        assert self._context is not None
        return self._context.__exit__(exc_type, exc, tb)
