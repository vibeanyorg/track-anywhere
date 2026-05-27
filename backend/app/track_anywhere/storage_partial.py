from __future__ import annotations

from typing import Any

from .storage_changes import (
    AttachmentChanges,
    BookDirectoryChanges,
    CatalogChanges,
    CredentialChanges,
    CreditCardProfileChanges,
    DraftChanges,
    FinanceChanges,
    IdempotencyChanges,
    InvestmentChanges,
    LedgerChanges,
    PaymentProfileChanges,
    RecurringChanges,
    UserChanges,
    WriteMetadata,
)
from .storage_models import AccountRecord, AdjustmentAccountRecord, AssetRecord, CategoryRecord


class StorageMetadataWriters:
    def save_idempotency(self, changes: IdempotencyChanges) -> None:
        with self.unit_of_work() as uow:
            uow.credentials.save(changes.metadata.credentials)
            uow.idempotency.save_receipts(changes.metadata.idempotency_receipts)

    def save_credential_change(self, changes: CredentialChanges) -> None:
        with self.unit_of_work() as uow:
            self._save_write_metadata(uow, changes.metadata)

    @staticmethod
    def _save_write_metadata(uow, metadata: WriteMetadata) -> None:
        uow.credentials.save(metadata.credentials)
        uow.audit.save_events(metadata.audit_events)
        uow.idempotency.save_receipts(metadata.idempotency_receipts)


class CatalogChangeStorageWriters:
    def save_catalog_change(self, changes: CatalogChanges) -> None:
        with self.unit_of_work() as uow:
            uow.assets.save(changes.assets)
            uow.categories.save(changes.categories)
            uow.categories.save_history(
                aliases=changes.category_history.aliases,
                versions=changes.category_history.versions,
                events=changes.category_history.events,
            )
            uow.counterparties.save(changes.counterparties)
            uow.payment_instruments.save(changes.payment_instruments)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(
            categories=changes.categories,
            counterparties=changes.counterparties,
            payment_instruments=changes.payment_instruments,
        )


class LedgerChangeStorageWriters:
    def save_ledger_change(self, changes: LedgerChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.assets.save(changes.assets)
            uow.ledger.save_accounts(accounts)
            uow.ledger.save_transactions(changes.transactions)
            if changes.category_history is not None:
                uow.categories.save_history(
                    aliases=changes.category_history.aliases,
                    versions=changes.category_history.versions,
                    events=changes.category_history.events,
                )
            uow.counterparties.save(changes.counterparties)
            self._save_write_metadata(uow, changes.metadata)
            uow.ledger.save_adjustment_accounts(changes.adjustment_account_ids)
        self.update_read_cache(
            accounts=accounts,
            transactions=changes.transactions,
            counterparties=changes.counterparties,
        )


class DirectoryChangeStorageWriters:
    def save_user_change(self, changes: UserChanges) -> None:
        with self.unit_of_work() as uow:
            uow.users.save(changes.users)
            self._save_write_metadata(uow, changes.metadata)

    def save_book_change(self, changes: BookDirectoryChanges) -> None:
        with self.unit_of_work() as uow:
            uow.books.save(changes.book_changes.books, changes.book_changes.members)
            self._save_write_metadata(uow, changes.metadata)


class WorkflowChangeStorageWriters:
    def save_draft_change(self, changes: DraftChanges) -> None:
        with self.unit_of_work() as uow:
            uow.drafts.save(changes.drafts)
            uow.ledger.save_transactions(changes.transactions)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(transactions=changes.transactions, drafts=changes.drafts)

    def save_recurring_change(self, changes: RecurringChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.recurring.save_items(changes.items)
            uow.drafts.save(changes.drafts)
            uow.ledger.save_accounts(accounts)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(accounts=accounts, drafts=changes.drafts, recurring_items=changes.items)


class FinanceChangeStorageWriters:
    def save_finance_change(self, changes: FinanceChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.funds.save(changes.funds)
            if changes.budget_changes is not None:
                uow.budgets.save(changes.budget_changes.budgets, changes.budget_changes.targets)
            uow.ledger.save_accounts(accounts)
            uow.ledger.save_transactions(changes.transactions)
            uow.assets.save(changes.assets)
            self._save_write_metadata(uow, changes.metadata)
            uow.reconciliation.save(changes.actions)
        self.update_read_cache(accounts=accounts, transactions=changes.transactions)

    def save_investment_change(self, changes: InvestmentChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.investments.save_events(changes.events)
            uow.investments.save_valuations(changes.valuations)
            uow.ledger.save_accounts(accounts)
            uow.ledger.save_transactions(changes.transactions)
            uow.assets.save(changes.assets)
            self._save_write_metadata(uow, changes.metadata)
            uow.ledger.save_adjustment_accounts(changes.adjustment_account_ids)
        self.update_read_cache(accounts=accounts, transactions=changes.transactions)


class ProfileChangeStorageWriters:
    def save_credit_card_profile_change(self, changes: CreditCardProfileChanges) -> None:
        with self.unit_of_work() as uow:
            uow.credit_cards.save_profiles(changes.profiles)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(credit_card_profiles=changes.profiles)

    def save_payment_profile_change(self, changes: PaymentProfileChanges) -> None:
        with self.unit_of_work() as uow:
            uow.payment_profiles.save(changes.profiles)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(payment_profiles=changes.profiles)

    def save_attachment_change(self, changes: AttachmentChanges) -> None:
        with self.unit_of_work() as uow:
            uow.attachments.save(changes.attachments)
            uow.drafts.save(changes.drafts)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(drafts=changes.drafts)


class CoreEntityStorageWriters:
    def _save_assets(self, session, assets) -> None:
        for asset in assets:
            self._upsert_record(
                session,
                AssetRecord,
                {
                    "asset_code": asset.asset_code,
                    "kind": asset.kind,
                    "scale": asset.scale,
                    "display_scale": asset.display_scale if asset.display_scale is not None else asset.scale,
                    "name": asset.name,
                    "status": asset.status,
                    "version": asset.version,
                },
                ["asset_code"],
            )

    def _save_accounts(self, session, accounts) -> None:
        for account in accounts:
            self._upsert_record(
                session,
                AccountRecord,
                {
                    "account_id": account.account_id,
                    "book_id": account.book_id,
                    "name": account.name,
                    "type": account.type,
                    "currency": account.currency,
                    "institution_type": account.institution_type,
                    "subtype": account.subtype,
                    "institution": account.institution,
                    "version": account.version,
                },
                ["account_id"],
            )

    def _save_categories(self, session, categories) -> None:
        for category in categories:
            self._upsert_record(
                session,
                CategoryRecord,
                {
                    "category_id": category.category_id,
                    "book_id": category.book_id,
                    "kind": category.kind,
                    "parent_id": category.parent_id,
                    "name": category.name,
                    "normalized_name": category.normalized_name,
                    "level": category.level,
                    "path_cache": category.path_cache,
                    "icon": category.icon,
                    "color": category.color,
                    "sort_order": category.sort_order,
                    "status": category.status,
                    "version": category.version,
                },
                ["category_id"],
            )


class PartialStorageWriters(
    StorageMetadataWriters,
    CatalogChangeStorageWriters,
    LedgerChangeStorageWriters,
    DirectoryChangeStorageWriters,
    WorkflowChangeStorageWriters,
    FinanceChangeStorageWriters,
    ProfileChangeStorageWriters,
    CoreEntityStorageWriters,
):
    pass
