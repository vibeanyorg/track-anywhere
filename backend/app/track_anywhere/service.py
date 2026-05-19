from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import Any

from .audit import AuditLog
from .attachments import AttachmentIntake
from .auth_identities import AuthIdentityDirectory
from .books import DEFAULT_BOOK_ID, BookDirectory
from .budgets import BudgetBook
from .categories import CategoryBook
from .credit_cards import CreditCardBook
from .drafts import DraftBook
from .idempotency import IdempotencyStore
from .investments import InvestmentBook
from .ledger import Ledger
from .recurring import RecurringBook
from .security import Actor, CredentialStore, DeploymentSecurityConfig, validate_startup_security
from .service_auth import OWNER_SCOPES
from .service_balances import BalanceUseCases
from .service_books import BookUseCases
from .service_catalog import CatalogUseCases
from .service_credentials import CredentialUseCases
from .service_drafts import DraftUseCases
from .service_finance import FinancialUseCases
from .service_identity import IdentityUseCases
from .service_ledger import LedgerUseCases
from .service_recurring import RecurringUseCases
from .storage import OrmStorage, new_owner_token
from .users import UserDirectory


class FinanceService(
    IdentityUseCases,
    BookUseCases,
    CatalogUseCases,
    FinancialUseCases,
    CredentialUseCases,
    DraftUseCases,
    RecurringUseCases,
    BalanceUseCases,
    LedgerUseCases,
):
    def __init__(self, config: DeploymentSecurityConfig | None = None, *, database_url: str | None = None) -> None:
        self.config = config or DeploymentSecurityConfig()
        self.startup_warnings = validate_startup_security(self.config)
        self.storage = OrmStorage(database_url)
        self.credentials = CredentialStore()
        self.audit = AuditLog()
        self.idempotency = IdempotencyStore()
        self.books = BookDirectory()
        self.ledger = Ledger()
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
        self.storage.load_into(self)
        self._ensure_domain_foundations()
        self._ensure_owner_credential()
        self._persist()

    def actor_from_token(self, token: str, required_scope: str | None = None) -> Actor:
        return self.credentials.verify(token, required_scope=required_scope)

    def actor_for_book(self, token: str, book_id: str | None, required_scope: str | None = None) -> Actor:
        actor = self.actor_from_token(token, required_scope=required_scope)
        self.books.require_access(book_id, actor, required_scope)
        return actor

    def _ensure_owner_credential(self) -> None:
        try:
            actor = self.credentials.verify(self.owner_token)
            if OWNER_SCOPES.issubset(actor.scopes):
                return
        except Exception:
            pass
        self.credentials.issue(
            actor_id="owner",
            actor_type="human",
            scopes=set(OWNER_SCOPES),
            ttl=timedelta(days=30),
            token=self.owner_token,
        )

    def _persist(self) -> None:
        self.storage.save(self)

    def _ensure_domain_foundations(self) -> None:
        self.books.ensure_default()
        for account in self.ledger.accounts.values():
            account.book_id = account.book_id or DEFAULT_BOOK_ID
        for category in list(self.categories.categories.values()):
            category.book_id = category.book_id or DEFAULT_BOOK_ID
            if category.secondary is not None and category.parent_id is None:
                parent = self.categories.find(kind=category.kind, primary=category.primary, book_id=category.book_id)
                if parent is None:
                    parent = self.categories.create(kind=category.kind, primary=category.primary, book_id=category.book_id)
                category.parent_id = parent.category_id
                category.name = category.secondary
                self.categories._sync_legacy_fields(category)
            if not any(version.category_id == category.category_id for version in self.categories.versions.values()):
                self.categories._record_version(category, "migration")
        for transaction in self.ledger.transactions.values():
            transaction.book_id = transaction.book_id or self._book_id_for_transaction(transaction)
            if transaction.lines is None:
                transaction.lines = []
            if transaction.category_id is not None and not transaction.lines:
                self._add_category_line_for_transaction(transaction, self.categories.get(transaction.category_id))
        for draft in self.drafts.drafts.values():
            draft.book_id = draft.book_id or self._book_id_for_postings(draft.proposed_postings)
        for item in self.recurring.items.values():
            if not item.book_id and item.source_account_id is not None:
                item.book_id = self.ledger.get_account(item.source_account_id).book_id

    def _book_id_for_transaction(self, transaction) -> str:
        return self._book_id_for_postings(transaction.postings)

    def _book_id_for_postings(self, postings) -> str:
        for posting in postings:
            account = self.ledger.accounts.get(posting.account_id)
            if account is not None:
                return account.book_id
        return DEFAULT_BOOK_ID

    @staticmethod
    def _hash_command(command) -> str:
        return sha256(command.model_dump_json().encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_command_payload(command, extra: dict[str, Any]) -> str:
        payload = {**extra, **command.model_dump(mode="json", exclude_none=True)}
        return sha256(str(sorted(payload.items())).encode("utf-8")).hexdigest()
