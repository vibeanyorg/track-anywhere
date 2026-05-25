from __future__ import annotations

from typing import Any

from .storage_models import AccountRecord, AdjustmentAccountRecord, AssetRecord, CategoryRecord


class PartialStorageWriters:
    def save_idempotency(self, service: Any) -> None:
        dirty_credentials = service.credentials.dirty_credentials()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.session_factory.begin() as session:
            self._save_credentials(session, dirty_credentials)
            self._save_idempotency_receipts(session, dirty_receipts)
        service.credentials.mark_clean()
        service.idempotency.mark_clean()

    def save_catalog_change(self, service: Any) -> None:
        dirty_credentials = service.credentials.dirty_credentials()
        dirty_assets = service.assets.dirty_assets()
        dirty_categories = service.categories.dirty_categories()
        dirty_aliases, dirty_versions, dirty_events = service.categories.dirty_history()
        pending_events = service.audit.pending_events()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.session_factory.begin() as session:
            self._save_assets(session, dirty_assets)
            self._save_categories(session, dirty_categories)
            self._save_category_history(
                session,
                service.categories,
                aliases=dirty_aliases,
                versions=dirty_versions,
                events=dirty_events,
            )
            self._save_credentials(session, dirty_credentials)
            self._save_audit_events(session, pending_events)
            self._save_idempotency_receipts(session, dirty_receipts)
        service.credentials.mark_clean()
        service.assets.mark_clean()
        service.categories.mark_clean()
        service.audit.mark_persisted()
        service.idempotency.mark_clean()

    def save_ledger_change(self, service: Any, transactions, *, include_category_history: bool = False) -> None:
        dirty_credentials = service.credentials.dirty_credentials()
        dirty_assets = service.assets.dirty_assets()
        dirty_accounts = service.ledger.dirty_accounts()
        dirty_aliases, dirty_versions, dirty_events = service.categories.dirty_history()
        pending_events = service.audit.pending_events()
        dirty_receipts = service.idempotency.dirty_receipts()
        with self.session_factory.begin() as session:
            self._save_assets(session, dirty_assets)
            self._save_accounts(session, dirty_accounts)
            self._save_transactions(session, transactions)
            if include_category_history:
                self._save_category_history(
                    session,
                    service.categories,
                    aliases=dirty_aliases,
                    versions=dirty_versions,
                    events=dirty_events,
                )
            self._save_credentials(session, dirty_credentials)
            self._save_audit_events(session, pending_events)
            self._save_idempotency_receipts(session, dirty_receipts)
            for currency, account_id in service.adjustment_account_ids.items():
                session.merge(AdjustmentAccountRecord(currency=currency, account_id=account_id))
        service.credentials.mark_clean()
        service.assets.mark_clean()
        service.ledger.mark_accounts_clean()
        if include_category_history:
            service.categories.mark_clean()
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
