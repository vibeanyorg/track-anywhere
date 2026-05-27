from __future__ import annotations

from .storage_changes import BookChanges, BudgetChanges, CategoryHistoryChanges, WriteMetadata


class ServiceStartupPersistence:
    def _persist_startup_maintenance(self) -> None:
        self.storage.save_startup_maintenance(
            book_changes=self._book_changes(),
            assets=self.assets.dirty_assets(),
            categories=self.categories.dirty_categories(),
            category_history=self._category_history_changes(),
            metadata=self._write_metadata(),
        )
        self.books.mark_clean()
        self.assets.mark_clean()
        self.credentials.mark_clean()
        self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceMetadataPersistence:
    def _persist_idempotency(self) -> None:
        self.storage.save_idempotency(self._write_metadata())
        self.credentials.mark_clean()
        self.idempotency.mark_clean()

    def _persist_credential_change(self) -> None:
        self.storage.save_credential_change(metadata=self._write_metadata())
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_replay_or(self, replay: bool, persist) -> None:
        if replay:
            self._persist_idempotency()
        else:
            persist()


class ServiceCatalogPersistence:
    def _persist_catalog_change(self) -> None:
        counterparties = self.counterparties.dirty_counterparties()
        payment_instruments = self.payment_instruments.dirty_instruments()
        self.storage.save_catalog_change(
            metadata=self._write_metadata(),
            assets=self.assets.dirty_assets(),
            categories=self.categories.dirty_categories(),
            category_history=self._category_history_changes(),
            counterparties=counterparties,
            payment_instruments=payment_instruments,
        )
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.categories.mark_clean()
        self.counterparties.mark_clean()
        self.payment_instruments.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceLedgerPersistence:
    def _persist_ledger_change(self, *transactions, accounts=(), include_category_history: bool = False) -> None:
        self.storage.save_ledger_change(
            transactions,
            metadata=self._write_metadata(),
            accounts=accounts,
            assets=self.assets.dirty_assets(),
            adjustment_account_ids=self.adjustment_account_ids,
            category_history=self._category_history_changes() if include_category_history else None,
            counterparties=self.counterparties.dirty_counterparties(),
        )
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.counterparties.mark_clean()
        if include_category_history:
            self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_reclassification_change(self, transaction, line_id: str) -> None:
        self.storage.save_reclassification_change(
            transaction,
            line_id,
            category_history=self._category_history_changes(),
            metadata=self._write_metadata(),
        )
        self.storage.update_read_cache(transactions=(transaction,))
        self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceDirectoryPersistence:
    def _persist_user_change(self, *users) -> None:
        self.storage.save_user_change(users, metadata=self._write_metadata())
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_book_change(self) -> None:
        self.storage.save_book_change(self._book_changes(), metadata=self._write_metadata())
        self.books.mark_clean()
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceWorkflowPersistence:
    def _persist_draft_change(self, *drafts, transactions=()) -> None:
        self.storage.save_draft_change(drafts, transactions=transactions, metadata=self._write_metadata())
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_recurring_change(self, *items, drafts=(), accounts=()) -> None:
        self.storage.save_recurring_change(items, drafts=drafts, accounts=accounts, metadata=self._write_metadata())
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceFinancePersistence:
    def _persist_finance_change(self, *, funds=(), budgets: bool = False, transactions=(), accounts=(), actions=()) -> None:
        self.storage.save_finance_change(
            metadata=self._write_metadata(),
            funds=funds,
            budget_changes=self._budget_changes() if budgets else None,
            transactions=transactions,
            accounts=accounts,
            assets=self.assets.dirty_assets(),
            actions=actions,
        )
        if budgets:
            self.budgets.mark_clean()
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_investment_change(self, *, events=(), valuations=(), transactions=(), accounts=()) -> None:
        self.storage.save_investment_change(
            metadata=self._write_metadata(),
            events=events,
            valuations=valuations,
            transactions=transactions,
            accounts=accounts,
            assets=self.assets.dirty_assets(),
            adjustment_account_ids=self.adjustment_account_ids,
        )
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceProfilePersistence:
    def _persist_credit_card_profile_change(self, *profiles) -> None:
        self.storage.save_credit_card_profile_change(profiles, metadata=self._write_metadata())
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_payment_profile_change(self) -> None:
        profiles = self.payment_profiles.dirty_profiles()
        self.storage.save_payment_profile_change(profiles, metadata=self._write_metadata())
        self.payment_profiles.mark_clean()
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _persist_attachment_change(self, *, attachments=(), drafts=()) -> None:
        self.storage.save_attachment_change(metadata=self._write_metadata(), attachments=attachments, drafts=drafts)
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceChangeSetCollectors:
    def _write_metadata(self) -> WriteMetadata:
        return WriteMetadata(
            credentials=tuple(self.credentials.dirty_credentials()),
            audit_events=tuple(self.audit.pending_events()),
            idempotency_receipts=tuple(self.idempotency.dirty_receipts()),
        )

    def _category_history_changes(self) -> CategoryHistoryChanges:
        aliases, versions, events = self.categories.dirty_history()
        return CategoryHistoryChanges(
            aliases=tuple(aliases),
            versions=tuple(versions),
            events=tuple(events),
        )

    def _book_changes(self) -> BookChanges:
        return BookChanges(
            books=tuple(self.books.dirty_books()),
            members=tuple(self.books.dirty_members()),
        )

    def _budget_changes(self) -> BudgetChanges:
        return BudgetChanges(
            budgets=tuple(self.budgets.dirty_budgets()),
            targets=tuple(self.budgets.dirty_targets()),
        )


class ServicePersistenceMixin(
    ServiceStartupPersistence,
    ServiceMetadataPersistence,
    ServiceCatalogPersistence,
    ServiceLedgerPersistence,
    ServiceDirectoryPersistence,
    ServiceWorkflowPersistence,
    ServiceFinancePersistence,
    ServiceProfilePersistence,
    ServiceChangeSetCollectors,
):
    pass
