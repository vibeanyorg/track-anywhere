from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from .attachments import Attachment
from .categories import Category
from .credit_cards import CreditCardProfile
from .db_migrations import run_migrations
from .ledger import Account
from .storage_engine import create_database_engine, database_url_from_env
from .storage_json import new_owner_token, to_jsonable
from .storage_loaders import StorageLoaders
from .storage_models import (
    AdjustmentAccountRecord,
    AppStateRecord,
    AttachmentRecord,
    Base,
    CategoryRecord,
    CreditCardProfileRecord,
    ReconciliationActionRecord,
    UserRecord,
    AccountRecord,
)
from .storage_writers import StorageWriters
from .users import AppUser


class OrmStorage(StorageLoaders, StorageWriters):
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or database_url_from_env()
        self.engine = create_database_engine(self.database_url)
        run_migrations(self.engine, Base.metadata)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def load_into(self, service: Any) -> None:
        with self.session_factory() as session:
            service.ledger.accounts = {
                row.account_id: Account(
                    account_id=row.account_id,
                    name=row.name,
                    type=row.type,
                    currency=row.currency,
                    institution_type=row.institution_type,
                    subtype=row.subtype,
                    institution=row.institution,
                    version=row.version,
                )
                for row in session.query(AccountRecord).all()
            }
            service.users.users = {
                row.user_id: AppUser(
                    user_id=row.user_id,
                    username=row.username,
                    display_name=row.display_name,
                    version=row.version,
                )
                for row in session.query(UserRecord).all()
            }
            service.ledger.transactions = self._load_transactions(session)
            service.drafts.drafts = self._load_drafts(session)
            service.recurring.items = self._load_recurring_items(session)
            service.budgets.funds = self._load_funds(session)
            service.investments.events = self._load_investment_events(session)
            service.categories.categories = {
                row.category_id: Category(
                    category_id=row.category_id,
                    kind=row.kind,
                    primary=row.primary,
                    secondary=row.secondary,
                    version=row.version,
                )
                for row in session.query(CategoryRecord).all()
            }
            service.credit_cards.profiles = {
                row.account_id: CreditCardProfile(
                    account_id=row.account_id,
                    credit_limit=Decimal(row.credit_limit) if row.credit_limit is not None else None,
                    available_credit=Decimal(row.available_credit) if row.available_credit is not None else None,
                    statement_day=row.statement_day,
                    due_day=row.due_day,
                    annual_fee=Decimal(row.annual_fee) if row.annual_fee is not None else None,
                    version=row.version,
                )
                for row in session.query(CreditCardProfileRecord).all()
            }
            service.attachments.attachments = {
                row.attachment_id: Attachment(
                    attachment_id=row.attachment_id,
                    storage_key=row.storage_key,
                    content_hash=row.content_hash,
                    mime_type=row.mime_type,
                    original_filename=row.original_filename,
                    scanner_status=row.scanner_status,
                )
                for row in session.query(AttachmentRecord).all()
            }
            service.credentials._credentials = self._load_credentials(session)
            service.audit.events = self._load_audit_events(session)
            service.idempotency._receipts = self._load_idempotency_receipts(session)
            service.reconciliation_actions = [row.payload for row in session.query(ReconciliationActionRecord).all()]
            service.adjustment_account_ids = {
                row.currency: row.account_id for row in session.query(AdjustmentAccountRecord).all()
            }
            owner_state = session.get(AppStateRecord, "owner_token")
            if owner_state is not None:
                service.owner_token = str(owner_state.value["token"])

    def save(self, service: Any) -> None:
        with self.session_factory.begin() as session:
            session.execute(delete(AppStateRecord).where(AppStateRecord.key == "owner_token"))
            for account in service.ledger.accounts.values():
                session.merge(
                    AccountRecord(
                        account_id=account.account_id,
                        name=account.name,
                        type=account.type,
                        currency=account.currency,
                        institution_type=account.institution_type,
                        subtype=account.subtype,
                        institution=account.institution,
                        version=account.version,
                    )
                )
            for user in service.users.users.values():
                session.merge(
                    UserRecord(
                        user_id=user.user_id,
                        username=user.username,
                        display_name=user.display_name,
                        version=user.version,
                    )
                )
            self._save_transactions(session, service.ledger.transactions.values())
            self._save_drafts(session, service.drafts.drafts.values())
            self._save_recurring_items(session, service.recurring.items.values())
            self._save_funds(session, service.budgets.funds.values())
            self._save_investment_events(session, service.investments.events.values())
            for category in service.categories.categories.values():
                session.merge(
                    CategoryRecord(
                        category_id=category.category_id,
                        kind=category.kind,
                        primary=category.primary,
                        secondary=category.secondary,
                        version=category.version,
                    )
                )
            for profile in service.credit_cards.profiles.values():
                session.merge(
                    CreditCardProfileRecord(
                        account_id=profile.account_id,
                        credit_limit=str(profile.credit_limit) if profile.credit_limit is not None else None,
                        available_credit=str(profile.available_credit) if profile.available_credit is not None else None,
                        statement_day=profile.statement_day,
                        due_day=profile.due_day,
                        annual_fee=str(profile.annual_fee) if profile.annual_fee is not None else None,
                        version=profile.version,
                    )
                )
            for attachment in service.attachments.attachments.values():
                session.merge(
                    AttachmentRecord(
                        attachment_id=attachment.attachment_id,
                        storage_key=attachment.storage_key,
                        content_hash=attachment.content_hash,
                        mime_type=attachment.mime_type,
                        original_filename=attachment.original_filename,
                        scanner_status=attachment.scanner_status,
                    )
                )
            self._save_credentials(session, service.credentials._credentials.values())
            self._save_audit_events(session, service.audit.events)
            self._save_idempotency_receipts(session, service.idempotency._receipts.values())
            for action in service.reconciliation_actions:
                session.merge(
                    ReconciliationActionRecord(
                        reconciliation_id=str(action["reconciliation_id"]),
                        payload=to_jsonable(action),
                    )
                )
            for currency, account_id in service.adjustment_account_ids.items():
                session.merge(AdjustmentAccountRecord(currency=currency, account_id=account_id))


__all__ = [
    "Base",
    "OrmStorage",
    "create_database_engine",
    "database_url_from_env",
    "new_owner_token",
    "to_jsonable",
]
