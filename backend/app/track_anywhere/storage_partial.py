from __future__ import annotations

from typing import Any

from .storage_changes import BookChanges, BudgetChanges, CategoryHistoryChanges, WriteMetadata
from .storage_models import AccountRecord, AdjustmentAccountRecord, AssetRecord, CategoryRecord


class StorageMetadataWriters:
    def save_idempotency(self, metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            uow.credentials.save(metadata.credentials)
            uow.idempotency.save_receipts(metadata.idempotency_receipts)

    def save_credential_change(self, *, metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            self._save_write_metadata(uow, metadata)

    @staticmethod
    def _save_write_metadata(uow, metadata: WriteMetadata) -> None:
        uow.credentials.save(metadata.credentials)
        uow.audit.save_events(metadata.audit_events)
        uow.idempotency.save_receipts(metadata.idempotency_receipts)


class CatalogChangeStorageWriters:
    def save_catalog_change(
        self,
        *,
        metadata: WriteMetadata,
        assets=(),
        categories=(),
        category_history: CategoryHistoryChanges | None = None,
        counterparties=(),
        payment_instruments=(),
    ) -> None:
        with self.unit_of_work() as uow:
            uow.assets.save(assets)
            uow.categories.save(categories)
            if category_history is not None:
                uow.categories.save_history(
                    aliases=category_history.aliases,
                    versions=category_history.versions,
                    events=category_history.events,
                )
            uow.counterparties.save(counterparties)
            uow.payment_instruments.save(payment_instruments)
            self._save_write_metadata(uow, metadata)
        self.update_read_cache(
            categories=categories,
            counterparties=counterparties,
            payment_instruments=payment_instruments,
        )


class LedgerChangeStorageWriters:
    def save_ledger_change(
        self,
        transactions,
        *,
        metadata: WriteMetadata,
        accounts=(),
        assets=(),
        adjustment_account_ids: dict[str, str] | None = None,
        category_history: CategoryHistoryChanges | None = None,
        counterparties=(),
    ) -> None:
        accounts = list(accounts)
        with self.unit_of_work() as uow:
            uow.assets.save(assets)
            uow.ledger.save_accounts(accounts)
            uow.ledger.save_transactions(transactions)
            if category_history is not None:
                uow.categories.save_history(
                    aliases=category_history.aliases,
                    versions=category_history.versions,
                    events=category_history.events,
                )
            uow.counterparties.save(counterparties)
            self._save_write_metadata(uow, metadata)
            uow.ledger.save_adjustment_accounts(adjustment_account_ids or {})
        self.update_read_cache(accounts=accounts, transactions=transactions, counterparties=counterparties)


class DirectoryChangeStorageWriters:
    def save_user_change(self, users, *, metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            uow.users.save(users)
            self._save_write_metadata(uow, metadata)

    def save_book_change(self, book_changes: BookChanges, *, metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            uow.books.save(book_changes.books, book_changes.members)
            self._save_write_metadata(uow, metadata)


class WorkflowChangeStorageWriters:
    def save_draft_change(self, drafts, *, transactions=(), metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            uow.drafts.save(drafts)
            uow.ledger.save_transactions(transactions)
            self._save_write_metadata(uow, metadata)
        self.update_read_cache(transactions=transactions, drafts=drafts)

    def save_recurring_change(self, items, *, drafts=(), accounts=(), metadata: WriteMetadata) -> None:
        accounts = list(accounts)
        with self.unit_of_work() as uow:
            uow.recurring.save_items(items)
            uow.drafts.save(drafts)
            uow.ledger.save_accounts(accounts)
            self._save_write_metadata(uow, metadata)
        self.update_read_cache(accounts=accounts, drafts=drafts, recurring_items=items)


class FinanceChangeStorageWriters:
    def save_finance_change(
        self,
        *,
        metadata: WriteMetadata,
        funds=(),
        budget_changes: BudgetChanges | None = None,
        transactions=(),
        accounts=(),
        assets=(),
        actions=(),
    ) -> None:
        accounts = list(accounts)
        with self.unit_of_work() as uow:
            uow.funds.save(funds)
            if budget_changes is not None:
                uow.budgets.save(budget_changes.budgets, budget_changes.targets)
            uow.ledger.save_accounts(accounts)
            uow.ledger.save_transactions(transactions)
            uow.assets.save(assets)
            self._save_write_metadata(uow, metadata)
            uow.reconciliation.save(actions)
        self.update_read_cache(accounts=accounts, transactions=transactions)

    def save_investment_change(
        self,
        *,
        metadata: WriteMetadata,
        events=(),
        valuations=(),
        transactions=(),
        accounts=(),
        assets=(),
        adjustment_account_ids: dict[str, str] | None = None,
    ) -> None:
        accounts = list(accounts)
        with self.unit_of_work() as uow:
            uow.investments.save_events(events)
            uow.investments.save_valuations(valuations)
            uow.ledger.save_accounts(accounts)
            uow.ledger.save_transactions(transactions)
            uow.assets.save(assets)
            self._save_write_metadata(uow, metadata)
            uow.ledger.save_adjustment_accounts(adjustment_account_ids or {})
        self.update_read_cache(accounts=accounts, transactions=transactions)


class ProfileChangeStorageWriters:
    def save_credit_card_profile_change(self, profiles, *, metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            uow.credit_cards.save_profiles(profiles)
            self._save_write_metadata(uow, metadata)
        self.update_read_cache(credit_card_profiles=profiles)

    def save_payment_profile_change(self, profiles, *, metadata: WriteMetadata) -> None:
        with self.unit_of_work() as uow:
            uow.payment_profiles.save(profiles)
            self._save_write_metadata(uow, metadata)
        self.update_read_cache(payment_profiles=profiles)

    def save_attachment_change(self, *, metadata: WriteMetadata, attachments=(), drafts=()) -> None:
        with self.unit_of_work() as uow:
            uow.attachments.save(attachments)
            uow.drafts.save(drafts)
            self._save_write_metadata(uow, metadata)
        self.update_read_cache(drafts=drafts)


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
