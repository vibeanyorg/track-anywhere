from __future__ import annotations

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

class PartialStorageWriters(
    StorageMetadataWriters,
    CatalogChangeStorageWriters,
    LedgerChangeStorageWriters,
    DirectoryChangeStorageWriters,
    WorkflowChangeStorageWriters,
    FinanceChangeStorageWriters,
    ProfileChangeStorageWriters,
):
    pass
