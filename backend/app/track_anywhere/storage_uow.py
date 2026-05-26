from __future__ import annotations

from typing import Any, Iterable

from .storage_models import (
    AdjustmentAccountRecord,
    AppStateRecord,
    AttachmentRecord,
    AuthIdentityRecord,
    CreditCardProfileRecord,
    ReconciliationActionRecord,
    UserRecord,
)
from .storage_json import to_jsonable


class StorageUnitOfWork:
    def __init__(self, storage) -> None:
        self.storage = storage
        self._context = None
        self.session = None

    def __enter__(self):
        self._context = self.storage.session_factory.begin()
        self.session = self._context.__enter__()
        self.ledger = LedgerRepository(self.storage, self.session)
        self.catalog = CatalogRepository(self.storage, self.session)
        self.audit = AuditRepository(self.storage, self.session)
        self.idempotency = IdempotencyRepository(self.storage, self.session)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        assert self._context is not None
        return self._context.__exit__(exc_type, exc, tb)


class LedgerRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_accounts(self, accounts: Iterable[Any]) -> None:
        self.storage._save_accounts(self.session, accounts)

    def save_transactions(self, transactions: Iterable[Any]) -> None:
        self.storage._save_transactions(self.session, transactions)

    def save_adjustment_accounts(self, adjustment_account_ids: dict[str, str]) -> None:
        for currency, account_id in adjustment_account_ids.items():
            self.session.merge(AdjustmentAccountRecord(currency=currency, account_id=account_id))


class CatalogRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_assets(self, assets: Iterable[Any]) -> None:
        self.storage._save_assets(self.session, assets)

    def save_books(self, books) -> None:
        self.storage._save_books(self.session, books)

    def save_users(self, users: Iterable[Any]) -> None:
        for user in users:
            self.session.merge(
                UserRecord(
                    user_id=user.user_id,
                    username=user.username,
                    display_name=user.display_name,
                    version=user.version,
                )
            )

    def save_auth_identities(self, identities: Iterable[Any]) -> None:
        for identity in identities:
            self.session.merge(
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

    def save_categories(self, categories: Iterable[Any]) -> None:
        self.storage._save_categories(self.session, categories)

    def save_category_history(self, category_book, *, aliases=None, versions=None, events=None) -> None:
        self.storage._save_category_history(
            self.session,
            category_book,
            aliases=aliases,
            versions=versions,
            events=events,
        )

    def save_payment_instruments(self, service: Any) -> None:
        self.storage._save_payment_instruments(self.session, service, only_dirty=True)

    def save_payment_profiles(self, service: Any) -> None:
        self.storage._save_payment_profiles(self.session, service, only_dirty=True)

    def save_credit_card_profiles(self, profiles: Iterable[Any]) -> None:
        for profile in profiles:
            self.session.merge(
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

    def save_drafts(self, drafts: Iterable[Any]) -> None:
        self.storage._save_drafts(self.session, drafts)

    def save_recurring_items(self, items: Iterable[Any]) -> None:
        self.storage._save_recurring_items(self.session, items)

    def save_funds(self, funds: Iterable[Any]) -> None:
        self.storage._save_funds(self.session, funds)

    def save_budgets(self, budget_book) -> None:
        self.storage._save_budgets(self.session, budget_book)

    def save_investment_events(self, events: Iterable[Any]) -> None:
        self.storage._save_investment_events(self.session, events)

    def save_investment_valuations(self, valuations: Iterable[Any]) -> None:
        self.storage._save_investment_valuations(self.session, valuations)

    def save_attachments(self, attachments: Iterable[Any]) -> None:
        for attachment in attachments:
            self.session.merge(
                AttachmentRecord(
                    attachment_id=attachment.attachment_id,
                    storage_key=attachment.storage_key,
                    content_hash=attachment.content_hash,
                    mime_type=attachment.mime_type,
                    original_filename=attachment.original_filename,
                    scanner_status=attachment.scanner_status,
                )
            )

    def save_reconciliation_actions(self, actions: Iterable[dict[str, Any]]) -> None:
        for action in actions:
            self.session.merge(
                ReconciliationActionRecord(
                    reconciliation_id=str(action["reconciliation_id"]),
                    payload=to_jsonable(action),
                )
            )

    def save_owner_token(self, token: str) -> None:
        self.session.merge(AppStateRecord(key="owner_token", value={"token": token}))


class AuditRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        self.storage._save_audit_events(self.session, list(events))


class IdempotencyRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_credentials(self, credentials: Iterable[Any]) -> None:
        self.storage._save_credentials(self.session, credentials)

    def save_receipts(self, receipts: Iterable[Any]) -> None:
        self.storage._save_idempotency_receipts(self.session, receipts)
