from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete

from .storage_json import to_jsonable
from .storage_models import (
    AdjustmentAccountRecord,
    AppStateRecord,
    AttachmentRecord,
    AuthIdentityRecord,
    CreditCardProfileRecord,
    ReconciliationActionRecord,
    UserRecord,
)


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


class StateRepository:
    def __init__(self, session) -> None:
        self.session = session

    def delete_app_state(self, key: str) -> None:
        self.session.execute(delete(AppStateRecord).where(AppStateRecord.key == key))


class AssetRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, assets: Iterable[Any]) -> None:
        self.storage._save_assets(self.session, assets)


class BookRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, books: Iterable[Any], members: Iterable[Any]) -> None:
        self.storage._save_books(self.session, books, members)


class UserRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, users: Iterable[Any]) -> None:
        for user in users:
            self.session.merge(UserRecord(user_id=user.user_id, username=user.username, display_name=user.display_name, version=user.version))


class AuthIdentityRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, identities: Iterable[Any]) -> None:
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


class CategoryRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, categories: Iterable[Any]) -> None:
        self.storage._save_categories(self.session, categories)

    def save_history(self, *, aliases, versions, events) -> None:
        self.storage._save_category_history(self.session, aliases=aliases, versions=versions, events=events)


class CounterpartyRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, counterparties: Iterable[Any]) -> None:
        self.storage._save_counterparties(self.session, counterparties)


class PaymentInstrumentRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, instruments: Iterable[Any]) -> None:
        self.storage._save_payment_instruments(self.session, instruments)


class PaymentProfileRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, profiles: Iterable[Any]) -> None:
        self.storage._save_payment_profiles(self.session, profiles)


class CreditCardRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save_profiles(self, profiles: Iterable[Any]) -> None:
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


class DraftRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, drafts: Iterable[Any]) -> None:
        self.storage._save_drafts(self.session, drafts)


class RecurringRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_items(self, items: Iterable[Any]) -> None:
        self.storage._save_recurring_items(self.session, items)


class FundRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, funds: Iterable[Any]) -> None:
        self.storage._save_funds(self.session, funds)


class BudgetRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, budgets: Iterable[Any], targets: Iterable[Any]) -> None:
        self.storage._save_budgets(self.session, budgets, targets)


class InvestmentRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        self.storage._save_investment_events(self.session, events)

    def save_valuations(self, valuations: Iterable[Any]) -> None:
        self.storage._save_investment_valuations(self.session, valuations)


class AttachmentRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, attachments: Iterable[Any]) -> None:
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


class ReconciliationRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, actions: Iterable[dict[str, Any]]) -> None:
        for action in actions:
            self.session.merge(ReconciliationActionRecord(reconciliation_id=str(action["reconciliation_id"]), payload=to_jsonable(action)))


class AuditRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        self.storage._save_audit_events(self.session, list(events))


class CredentialRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, credentials: Iterable[Any]) -> None:
        self.storage._save_credentials(self.session, credentials)


class IdempotencyRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_receipts(self, receipts: Iterable[Any]) -> None:
        self.storage._save_idempotency_receipts(self.session, receipts)
