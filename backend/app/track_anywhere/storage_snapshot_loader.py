from __future__ import annotations

from decimal import Decimal

from .assets import AssetDefinition
from .attachments import Attachment
from .auth_identities import LinkedAuthIdentity
from .credit_cards import CreditCardProfile
from .storage_models import (
    AdjustmentAccountRecord,
    AppStateRecord,
    AssetRecord,
    AttachmentRecord,
    AuthIdentityRecord,
    CategoryRecord,
    CreditCardProfileRecord,
    ReconciliationActionRecord,
    UserRecord,
)
from .storage_repositories.categories import category_from_record
from .storage_snapshot import StorageSnapshot
from .users import AppUser


class StorageSnapshotLoader:
    def load_snapshot(self) -> StorageSnapshot:
        with self.session_factory() as session:
            books, book_members = self._load_books(session)
            budget_funds = self._load_funds(session)
            budgets, budget_targets = self._load_budgets(session)
            category_aliases, category_versions, classification_events = self._load_category_history(session)
            owner_state = session.get(AppStateRecord, "owner_token")
            return StorageSnapshot(
                books=books,
                book_members=book_members,
                assets={
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
                },
                users={
                    row.user_id: AppUser(
                        user_id=row.user_id,
                        username=row.username,
                        display_name=row.display_name,
                        version=row.version,
                    )
                    for row in session.query(UserRecord).all()
                },
                auth_identities={
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
                },
                drafts=self._load_drafts(session),
                recurring_items=self._load_recurring_items(session),
                budget_funds=budget_funds,
                budgets=budgets,
                budget_targets=budget_targets,
                counterparties=self._load_counterparties(session),
                payment_profiles=self._load_payment_profiles(session),
                payment_instruments=self._load_payment_instruments(session),
                investment_events=self._load_investment_events(session),
                investment_valuations=self._load_investment_valuations(session),
                categories={
                    row.category_id: category_from_record(row)
                    for row in session.query(CategoryRecord).all()
                },
                category_aliases=category_aliases,
                category_versions=category_versions,
                classification_events=classification_events,
                credit_card_profiles={
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
                },
                attachments={
                    row.attachment_id: Attachment(
                        attachment_id=row.attachment_id,
                        storage_key=row.storage_key,
                        content_hash=row.content_hash,
                        mime_type=row.mime_type,
                        original_filename=row.original_filename,
                        scanner_status=row.scanner_status,
                    )
                    for row in session.query(AttachmentRecord).all()
                },
                credentials=self._load_credentials(session),
                audit_events=self._load_audit_events(session),
                idempotency_receipts=self._load_idempotency_receipts(session),
                reconciliation_actions=[row.payload for row in session.query(ReconciliationActionRecord).all()],
                adjustment_account_ids={
                    row.currency: row.account_id
                    for row in session.query(AdjustmentAccountRecord).all()
                },
                owner_token=(str(owner_state.value["token"]) if owner_state is not None else None),
            )
