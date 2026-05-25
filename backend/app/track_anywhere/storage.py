from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from .assets import AssetDefinition
from .audit import AuditEvent
from .attachments import Attachment
from .auth_identities import LinkedAuthIdentity
from .categories import Category
from .credit_cards import CreditCardProfile
from .db_migrations import run_migrations
from .domain_storage_loaders import DomainStorageLoaders
from .domain_storage_writers import DomainStorageWriters
from . import storage_auth_models as _storage_auth_models
from .ledger import Account
from .storage_auth import AuthStorageWriters
from .storage_engine import create_database_engine, database_url_from_env
from .storage_json import new_owner_token, to_jsonable
from .storage_ledger_reads import LedgerReadStorage
from .storage_loaders import StorageLoaders
from .storage_partial import PartialStorageWriters
from .storage_models import (
    AdjustmentAccountRecord,
    AppStateRecord,
    AssetRecord,
    AttachmentRecord,
    AuthIdentityRecord,
    Base,
    CategoryRecord,
    CreditCardProfileRecord,
    ReconciliationActionRecord,
    UserRecord,
    AccountRecord,
)
from .storage_writers import StorageWriters
from .users import AppUser

_storage_auth_models.CredentialRecord


class OrmStorage(
    PartialStorageWriters,
    DomainStorageLoaders,
    StorageLoaders,
    LedgerReadStorage,
    AuthStorageWriters,
    DomainStorageWriters,
    StorageWriters,
):
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or database_url_from_env()
        self.engine = create_database_engine(self.database_url)
        run_migrations(self.engine, Base.metadata)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def save_audit_event(self, event: AuditEvent) -> None:
        with self.session_factory.begin() as session:
            self._save_audit_events(session, [event])

    def load_into(self, service: Any) -> None:
        with self.session_factory() as session:
            service.books.books, service.books.members = self._load_books(session)
            service.assets.assets.update({
                row.asset_code: AssetDefinition(
                    asset_code=row.asset_code,
                    kind=row.kind,
                    scale=row.scale,
                    name=row.name,
                    display_scale=getattr(row, "display_scale", row.scale),
                    status=row.status,
                    version=row.version,
                )
                for row in session.query(AssetRecord).all()
            })
            service.assets.ensure_defaults()
            service.ledger.accounts = {
                row.account_id: Account(
                    account_id=row.account_id,
                    book_id=row.book_id,
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
            service.ledger.mark_accounts_clean()
            service.users.users = {
                row.user_id: AppUser(
                    user_id=row.user_id,
                    username=row.username,
                    display_name=row.display_name,
                    version=row.version,
                )
                for row in session.query(UserRecord).all()
            }
            service.auth_identities.identities = {
                row.identity_id: LinkedAuthIdentity(
                    identity_id=row.identity_id,
                    provider=row.provider,
                    subject=row.subject,
                    user_id=row.user_id,
                    email=row.email,
                    email_verified=row.email_verified,
                    display_name=row.display_name,
                    picture_url=row.picture_url,
                    status=row.status,
                    version=row.version,
                )
                for row in session.query(AuthIdentityRecord).all()
            }
            service.ledger.transactions = self._load_transactions(session)
            service.drafts.drafts = self._load_drafts(session)
            service.recurring.items = self._load_recurring_items(session)
            service.budgets.funds = self._load_funds(session)
            service.budgets.budgets, service.budgets.targets = self._load_budgets(session)
            service.investments.events = self._load_investment_events(session)
            service.investments.valuations = self._load_investment_valuations(session)
            service.categories.categories = {
                row.category_id: Category(
                    category_id=row.category_id,
                    book_id=row.book_id,
                    kind=row.kind,
                    parent_id=row.parent_id,
                    name=row.name,
                    normalized_name=row.normalized_name,
                    level=row.level,
                    path_cache=row.path_cache,
                    icon=row.icon,
                    color=row.color,
                    sort_order=row.sort_order,
                    status=row.status,
                    version=row.version,
                )
                for row in session.query(CategoryRecord).all()
            }
            (
                service.categories.aliases,
                service.categories.versions,
                service.categories.events,
            ) = self._load_category_history(session)
            service.categories.mark_clean()
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
            service.credentials.mark_clean()
            service.audit.events = self._load_audit_events(session)
            service.audit.mark_persisted()
            service.idempotency._receipts = self._load_idempotency_receipts(session)
            service.idempotency.mark_clean()
            service.reconciliation_actions = [row.payload for row in session.query(ReconciliationActionRecord).all()]
            service.adjustment_account_ids = {
                row.currency: row.account_id for row in session.query(AdjustmentAccountRecord).all()
            }
            owner_state = session.get(AppStateRecord, "owner_token")
            if owner_state is not None:
                service.owner_token = str(owner_state.value["token"])
                service._startup_persist_required = True

    def save(self, service: Any) -> None:
        with self.session_factory.begin() as session:
            session.execute(delete(AppStateRecord).where(AppStateRecord.key == "owner_token"))
            self._save_books(session, service.books)
            self._save_assets(session, service.assets.assets.values())
            self._save_accounts(session, service.ledger.accounts.values())
            for user in service.users.users.values():
                session.merge(
                    UserRecord(
                        user_id=user.user_id,
                        username=user.username,
                        display_name=user.display_name,
                        version=user.version,
                    )
                )
            for identity in service.auth_identities.identities.values():
                session.merge(
                    AuthIdentityRecord(
                        identity_id=identity.identity_id,
                        provider=identity.provider,
                        subject=identity.subject,
                        user_id=identity.user_id,
                        email=identity.email,
                        email_verified=identity.email_verified,
                        display_name=identity.display_name,
                        picture_url=identity.picture_url,
                        status=identity.status,
                        version=identity.version,
                    )
                )
            self._save_transactions(session, service.ledger.transactions.values())
            self._save_drafts(session, service.drafts.drafts.values())
            self._save_recurring_items(session, service.recurring.items.values())
            self._save_funds(session, service.budgets.funds.values())
            self._save_budgets(session, service.budgets)
            self._save_investment_events(session, service.investments.events.values())
            self._save_investment_valuations(session, service.investments.valuations.values())
            self._save_categories(session, service.categories.categories.values())
            self._save_category_history(session, service.categories)
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
