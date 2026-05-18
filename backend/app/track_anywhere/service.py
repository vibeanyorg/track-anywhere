from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import Any

from .audit import AuditLog
from .attachments import AttachmentIntake
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
from .service_catalog import CatalogUseCases
from .service_credentials import CredentialUseCases
from .service_drafts import DraftUseCases
from .service_finance import FinancialUseCases
from .service_ledger import LedgerUseCases
from .service_recurring import RecurringUseCases
from .storage import OrmStorage, new_owner_token
from .users import UserDirectory


class FinanceService(
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
        self.ledger = Ledger()
        self.drafts = DraftBook()
        self.recurring = RecurringBook()
        self.budgets = BudgetBook()
        self.investments = InvestmentBook()
        self.categories = CategoryBook()
        self.credit_cards = CreditCardBook()
        self.attachments = AttachmentIntake(self.config)
        self.users = UserDirectory()
        self.reconciliation_actions: list[dict[str, Any]] = []
        self.adjustment_account_ids: dict[str, str] = {}
        self.owner_token = new_owner_token()
        self.storage.load_into(self)
        self._ensure_owner_credential()
        self._persist()

    def actor_from_token(self, token: str, required_scope: str | None = None) -> Actor:
        return self.credentials.verify(token, required_scope=required_scope)

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

    @staticmethod
    def _hash_command(command) -> str:
        return sha256(command.model_dump_json().encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_command_payload(command, extra: dict[str, Any]) -> str:
        payload = {**extra, **command.model_dump(mode="json", exclude_none=True)}
        return sha256(str(sorted(payload.items())).encode("utf-8")).hexdigest()
