from __future__ import annotations

from .storage_changes import (
    AttachmentChanges,
    BookChanges,
    BookDirectoryChanges,
    BudgetChanges,
    CatalogChanges,
    CategoryHistoryChanges,
    CredentialChanges,
    CreditCardProfileChanges,
    DraftChanges,
    FinanceChanges,
    IdempotencyChanges,
    InvestmentChanges,
    LedgerChanges,
    PaymentProfileChanges,
    ReclassificationChanges,
    RecurringChanges,
    StartupMaintenanceChanges,
    UserChanges,
    WriteMetadata,
)


class ServiceStartupPersistence:
    def _commit_startup_maintenance(self) -> None:
        changes = StartupMaintenanceChanges(
            book_changes=self._book_changes(),
            assets=tuple(self.assets.dirty_assets()),
            categories=tuple(self.categories.dirty_categories()),
            category_history=self._category_history_changes(),
            metadata=self._write_metadata(),
        )
        self.storage.save_startup_maintenance(changes)
        self.books.mark_clean()
        self.assets.mark_clean()
        self.credentials.mark_clean()
        self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceMetadataPersistence:
    def _commit_idempotency(self) -> None:
        self.storage.save_idempotency(IdempotencyChanges(metadata=self._write_metadata()))
        self.credentials.mark_clean()
        self.idempotency.mark_clean()

    def _commit_credential_change(self) -> None:
        self.storage.save_credential_change(CredentialChanges(metadata=self._write_metadata()))
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_replay_or(self, replay: bool, commit) -> None:
        if replay:
            self._commit_idempotency()
        else:
            commit()


class ServiceCatalogPersistence:
    def _commit_catalog_change(self) -> None:
        counterparties = self.counterparties.dirty_counterparties()
        payment_instruments = self.payment_instruments.dirty_instruments()
        changes = CatalogChanges(
            metadata=self._write_metadata(),
            assets=tuple(self.assets.dirty_assets()),
            categories=tuple(self.categories.dirty_categories()),
            category_history=self._category_history_changes(),
            counterparties=tuple(counterparties),
            payment_instruments=tuple(payment_instruments),
        )
        self.storage.save_catalog_change(changes)
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.categories.mark_clean()
        self.counterparties.mark_clean()
        self.payment_instruments.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceLedgerPersistence:
    def _commit_ledger_change(self, *transactions, accounts=(), include_category_history: bool = False) -> None:
        changes = LedgerChanges(
            transactions=tuple(transactions),
            metadata=self._write_metadata(),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            adjustment_account_ids=dict(self.adjustment_account_ids),
            category_history=self._category_history_changes() if include_category_history else None,
            counterparties=tuple(self.counterparties.dirty_counterparties()),
        )
        self.storage.save_ledger_change(changes)
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.counterparties.mark_clean()
        if include_category_history:
            self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_reclassification_change(self, transaction, line_id: str) -> None:
        changes = ReclassificationChanges(
            transaction=transaction,
            line_id=line_id,
            category_history=self._category_history_changes(),
            metadata=self._write_metadata(),
        )
        self.storage.save_reclassification_change(changes)
        self.storage.update_read_cache(transactions=(transaction,))
        self.categories.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceDirectoryPersistence:
    def _commit_user_change(self, *users) -> None:
        self.storage.save_user_change(UserChanges(users=tuple(users), metadata=self._write_metadata()))
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_book_change(self) -> None:
        changes = BookDirectoryChanges(book_changes=self._book_changes(), metadata=self._write_metadata())
        self.storage.save_book_change(changes)
        self.books.mark_clean()
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceWorkflowPersistence:
    def _commit_draft_change(self, *drafts, transactions=()) -> None:
        changes = DraftChanges(drafts=tuple(drafts), transactions=tuple(transactions), metadata=self._write_metadata())
        self.storage.save_draft_change(changes)
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_recurring_change(self, *items, drafts=(), accounts=()) -> None:
        changes = RecurringChanges(
            items=tuple(items),
            drafts=tuple(drafts),
            accounts=tuple(accounts),
            metadata=self._write_metadata(),
        )
        self.storage.save_recurring_change(changes)
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceFinancePersistence:
    def _commit_finance_change(
        self,
        *,
        funds=(),
        budgets: bool = False,
        transactions=(),
        accounts=(),
        actions=(),
    ) -> None:
        changes = FinanceChanges(
            metadata=self._write_metadata(),
            funds=tuple(funds),
            budget_changes=self._budget_changes() if budgets else None,
            transactions=tuple(transactions),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            actions=tuple(actions),
        )
        self.storage.save_finance_change(changes)
        if budgets:
            self.budgets.mark_clean()
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_investment_change(self, *, events=(), valuations=(), transactions=(), accounts=()) -> None:
        changes = InvestmentChanges(
            metadata=self._write_metadata(),
            events=tuple(events),
            valuations=tuple(valuations),
            transactions=tuple(transactions),
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            adjustment_account_ids=dict(self.adjustment_account_ids),
        )
        self.storage.save_investment_change(changes)
        self.credentials.mark_clean()
        self.assets.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()


class ServiceProfilePersistence:
    def _commit_credit_card_profile_change(self, *profiles) -> None:
        self.storage.save_credit_card_profile_change(
            CreditCardProfileChanges(profiles=tuple(profiles), metadata=self._write_metadata())
        )
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_payment_profile_change(self) -> None:
        profiles = self.payment_profiles.dirty_profiles()
        changes = PaymentProfileChanges(profiles=tuple(profiles), metadata=self._write_metadata())
        self.storage.save_payment_profile_change(changes)
        self.payment_profiles.mark_clean()
        self.credentials.mark_clean()
        self.audit.mark_persisted()
        self.idempotency.mark_clean()

    def _commit_attachment_change(self, *, attachments=(), drafts=()) -> None:
        changes = AttachmentChanges(
            metadata=self._write_metadata(),
            attachments=tuple(attachments),
            drafts=tuple(drafts),
        )
        self.storage.save_attachment_change(changes)
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
