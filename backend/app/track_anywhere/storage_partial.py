from __future__ import annotations

from typing import Any

from .storage_models import AccountRecord, AdjustmentAccountRecord, AssetRecord, CategoryRecord


class PartialStorageWriters:
    def save_idempotency(self, service: Any) -> None:
        dirty_credentials = service.credentials.dirty_credentials()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.unit_of_work() as uow:
            uow.idempotency.save_credentials(dirty_credentials)
            uow.idempotency.save_receipts(dirty_receipts)
        service.credentials.mark_clean()
        service.idempotency.mark_clean()

    def save_catalog_change(self, service: Any) -> None:
        dirty_credentials = service.credentials.dirty_credentials()
        dirty_assets = service.assets.dirty_assets()
        dirty_categories = service.categories.dirty_categories()
        dirty_payment_instruments = service.payment_instruments.dirty_instruments()
        dirty_aliases, dirty_versions, dirty_events = service.categories.dirty_history()
        pending_events = service.audit.pending_events()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.unit_of_work() as uow:
            uow.catalog.save_assets(dirty_assets)
            uow.catalog.save_categories(dirty_categories)
            uow.catalog.save_category_history(
                service.categories,
                aliases=dirty_aliases,
                versions=dirty_versions,
                events=dirty_events,
            )
            if hasattr(self, "_save_payment_instruments"):
                uow.catalog.save_payment_instruments(service)
            uow.idempotency.save_credentials(dirty_credentials)
            uow.audit.save_events(pending_events)
            uow.idempotency.save_receipts(dirty_receipts)
        self.update_read_cache(categories=dirty_categories, payment_instruments=dirty_payment_instruments)
        service.credentials.mark_clean()
        service.assets.mark_clean()
        service.categories.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_ledger_change(self, service: Any, transactions, *, accounts=(), include_category_history: bool = False) -> None:
        dirty_credentials = service.credentials.dirty_credentials()
        dirty_assets = service.assets.dirty_assets()
        dirty_accounts_by_id = {account.account_id: account for account in service.ledger.dirty_accounts()}
        dirty_accounts_by_id.update({account.account_id: account for account in accounts})
        dirty_accounts = list(dirty_accounts_by_id.values())
        dirty_aliases, dirty_versions, dirty_events = service.categories.dirty_history()
        pending_events = service.audit.pending_events()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.unit_of_work() as uow:
            uow.catalog.save_assets(dirty_assets)
            uow.ledger.save_accounts(dirty_accounts)
            uow.ledger.save_transactions(transactions)
            if include_category_history:
                uow.catalog.save_category_history(
                    service.categories,
                    aliases=dirty_aliases,
                    versions=dirty_versions,
                    events=dirty_events,
                )
            uow.idempotency.save_credentials(dirty_credentials)
            uow.audit.save_events(pending_events)
            uow.idempotency.save_receipts(dirty_receipts)
            uow.ledger.save_adjustment_accounts(service.adjustment_account_ids)
        self.update_read_cache(accounts=dirty_accounts, transactions=transactions)
        service.credentials.mark_clean()
        service.assets.mark_clean()
        service.ledger.mark_accounts_clean()
        if include_category_history:
            service.categories.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_user_change(self, service: Any, users) -> None:
        with self.unit_of_work() as uow:
            uow.catalog.save_users(users)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        service.credentials.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_book_change(self, service: Any) -> None:
        with self.unit_of_work() as uow:
            uow.catalog.save_books(service.books)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        service.credentials.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_draft_change(self, service: Any, drafts, *, transactions=()) -> None:
        dirty_accounts = service.ledger.dirty_accounts()
        with self.unit_of_work() as uow:
            uow.catalog.save_drafts(drafts)
            uow.ledger.save_transactions(transactions)
            uow.ledger.save_accounts(dirty_accounts)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        self.update_read_cache(accounts=dirty_accounts, transactions=transactions, drafts=drafts)
        service.credentials.mark_clean()
        service.ledger.mark_accounts_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_recurring_change(self, service: Any, items, *, drafts=()) -> None:
        dirty_accounts = service.ledger.dirty_accounts()
        with self.unit_of_work() as uow:
            uow.catalog.save_recurring_items(items)
            uow.catalog.save_drafts(drafts)
            uow.ledger.save_accounts(dirty_accounts)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        self.update_read_cache(accounts=dirty_accounts, drafts=drafts, recurring_items=items)
        service.credentials.mark_clean()
        service.ledger.mark_accounts_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_finance_change(self, service: Any, *, funds=(), budgets=False, transactions=(), actions=()) -> None:
        dirty_accounts = service.ledger.dirty_accounts()
        with self.unit_of_work() as uow:
            uow.catalog.save_funds(funds)
            if budgets:
                uow.catalog.save_budgets(service.budgets)
            uow.ledger.save_accounts(dirty_accounts)
            uow.ledger.save_transactions(transactions)
            uow.catalog.save_assets(service.assets.dirty_assets())
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
            uow.catalog.save_reconciliation_actions(actions)
        self.update_read_cache(accounts=dirty_accounts, transactions=transactions)
        service.credentials.mark_clean()
        service.assets.mark_clean()
        service.ledger.mark_accounts_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_investment_change(self, service: Any, *, events=(), valuations=(), transactions=()) -> None:
        dirty_accounts = service.ledger.dirty_accounts()
        with self.unit_of_work() as uow:
            uow.catalog.save_investment_events(events)
            uow.catalog.save_investment_valuations(valuations)
            uow.ledger.save_accounts(dirty_accounts)
            uow.ledger.save_transactions(transactions)
            uow.catalog.save_assets(service.assets.dirty_assets())
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
            uow.ledger.save_adjustment_accounts(service.adjustment_account_ids)
        self.update_read_cache(accounts=dirty_accounts, transactions=transactions)
        service.credentials.mark_clean()
        service.assets.mark_clean()
        service.ledger.mark_accounts_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_credit_card_profile_change(self, service: Any, profiles) -> None:
        with self.unit_of_work() as uow:
            uow.catalog.save_credit_card_profiles(profiles)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        self.update_read_cache(credit_card_profiles=profiles)
        service.credentials.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_payment_profile_change(self, service: Any) -> None:
        dirty_profiles = service.payment_profiles.dirty_profiles()
        with self.unit_of_work() as uow:
            uow.catalog.save_payment_profiles(service)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        self.update_read_cache(payment_profiles=dirty_profiles)
        service.credentials.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_credential_change(self, service: Any) -> None:
        with self.unit_of_work() as uow:
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        service.credentials.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_attachment_change(self, service: Any, *, attachments=(), drafts=()) -> None:
        with self.unit_of_work() as uow:
            uow.catalog.save_attachments(attachments)
            uow.catalog.save_drafts(drafts)
            uow.idempotency.save_credentials(service.credentials.dirty_credentials())
            uow.audit.save_events(service.audit.pending_events())
            uow.idempotency.save_receipts(service.idempotency.dirty_receipts())
        self.update_read_cache(drafts=drafts)
        service.credentials.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

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
