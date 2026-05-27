from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256
from typing import Any

from .assets import AssetCatalog
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
from .security import Actor, CredentialReference, CredentialStore, DeploymentSecurityConfig, validate_startup_security
from .service_assets import AssetUseCases
from .service_auth import OWNER_SCOPES
from .service_balances import BalanceUseCases
from .service_books import BookUseCases
from .service_catalog import CatalogUseCases
from .service_credentials import CredentialUseCases
from .service_drafts import DraftUseCases
from .service_finance import FinancialUseCases
from .service_fx import FxUseCases
from .service_identity import IdentityUseCases
from .service_investments import InvestmentUseCases
from .service_ledger import LedgerUseCases
from .service_reclassification import ReclassificationUseCases
from .service_reports import BookReportUseCases
from .service_recurring import RecurringUseCases
from .storage import OrmStorage, new_owner_token
from .storage_json import to_jsonable
from .payment_instruments import PaymentInstrumentDirectory
from .payment_profiles import PaymentProfileDirectory
from .users import UserDirectory


class FinanceService(
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
        self.storage.load_into(self)
        self._ensure_domain_foundations()
        self._ensure_owner_credential()
        if persist_on_initialize or self._startup_persist_required:
            self._persist_startup_maintenance()
        self.storage.refresh_read_cache_from_service(self)

    def actor_from_token(self, token: str | CredentialReference, required_scope: str | None = None) -> Actor:
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
            auth_kind="owner",
            name="Owner credential",
        )

    def _persist_startup_maintenance(self) -> None:
        self.storage.save_startup_maintenance(self)
        self.assets.mark_clean()
        self.credentials.mark_clean()
        self.ledger.mark_accounts_clean()
        self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_idempotency(self) -> None:
        self.storage.save_idempotency(self)

    def _persist_catalog_change(self) -> None:
        self.storage.save_catalog_change(self)

    def _persist_ledger_change(self, *transactions, accounts=(), include_category_history: bool = False) -> None:
        self.storage.save_ledger_change(
            self,
            transactions,
            accounts=accounts,
            include_category_history=include_category_history,
        )

    def _persist_reclassification_change(self, transaction, line_id: str) -> None:
        self.storage.save_reclassification_change(self, transaction, line_id)
        self.storage.update_read_cache(transactions=(transaction,))

    def _persist_user_change(self, *users) -> None:
        self.storage.save_user_change(self, users)

    def _persist_book_change(self) -> None:
        self.storage.save_book_change(self)

    def _persist_draft_change(self, *drafts, transactions=()) -> None:
        self.storage.save_draft_change(self, drafts, transactions=transactions)

    def _persist_recurring_change(self, *items, drafts=()) -> None:
        self.storage.save_recurring_change(self, items, drafts=drafts)

    def _persist_finance_change(self, *, funds=(), budgets: bool = False, transactions=(), actions=()) -> None:
        self.storage.save_finance_change(
            self,
            funds=funds,
            budgets=budgets,
            transactions=transactions,
            actions=actions,
        )

    def _persist_investment_change(self, *, events=(), valuations=(), transactions=()) -> None:
        self.storage.save_investment_change(self, events=events, valuations=valuations, transactions=transactions)

    def _persist_credit_card_profile_change(self, *profiles) -> None:
        self.storage.save_credit_card_profile_change(self, profiles)

    def _persist_payment_profile_change(self) -> None:
        self.storage.save_payment_profile_change(self)

    def _persist_credential_change(self) -> None:
        self.storage.save_credential_change(self)

    def _persist_attachment_change(self, *, attachments=(), drafts=()) -> None:
        self.storage.save_attachment_change(self, attachments=attachments, drafts=drafts)

    def _persist_replay_or(self, replay: bool, persist) -> None:
        if replay:
            self._persist_idempotency()
        else:
            persist()

    def _ensure_domain_foundations(self) -> None:
        self.books.ensure_default()
        self.assets.ensure_defaults()
        for book in self.books.books.values():
            self.assets.ensure(book.base_currency)
        for account in self.ledger.accounts.values():
            account.book_id = account.book_id or DEFAULT_BOOK_ID
            self.assets.ensure(account.currency)
        for category in list(self.categories.categories.values()):
            category.book_id = category.book_id or DEFAULT_BOOK_ID
            self.categories._sync_display_fields(category)
            if not any(version.category_id == category.category_id for version in self.categories.versions.values()):
                self.categories._record_version(category, "migration")
        for transaction in self.ledger.transactions.values():
            transaction.book_id = transaction.book_id or self._book_id_for_transaction(transaction)
            if transaction.lines is None:
                transaction.lines = []
            for posting in transaction.postings:
                self.assets.ensure(posting.currency)
            for line in transaction.lines:
                self.assets.ensure(line.currency)
            self.ledger.validate_transaction_integrity(transaction, enforce_asset_scale=False)
            if transaction.reverses_transaction_id:
                original = self.ledger.transactions.get(transaction.reverses_transaction_id)
                if original is not None and original.reversed_by is None:
                    original.reversed_by = transaction.transaction_id
        for draft in self.drafts.drafts.values():
            draft.book_id = draft.book_id or self._book_id_for_postings(draft.proposed_postings)
        for item in self.recurring.items.values():
            if not item.book_id and item.source_account_id is not None:
                item.book_id = self.storage.get_account(item.source_account_id).book_id
            if item.currency is not None:
                self.assets.ensure(item.currency)
        for event in self.investments.events.values():
            account = self.ledger.accounts.get(event.account_id)
            if account is not None:
                event.book_id = event.book_id or account.book_id
            self.assets.ensure(event.currency)
        for valuation in self.investments.valuations.values():
            account = self.ledger.accounts.get(valuation.account_id)
            if account is not None:
                valuation.book_id = valuation.book_id or account.book_id
            self.assets.ensure(valuation.currency)
        for budget in self.budgets.budgets.values():
            self.assets.ensure(budget.currency)

    def _book_id_for_transaction(self, transaction) -> str:
        return self._book_id_for_postings(transaction.postings)

    def _book_id_for_postings(self, postings) -> str:
        for posting in postings:
            account = self.ledger.accounts.get(posting.account_id)
            if account is not None:
                return account.book_id
        return DEFAULT_BOOK_ID

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
