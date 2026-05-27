from __future__ import annotations

from ..storage_changes import DraftChanges, RecurringChanges


class WorkflowChangeStorageWriters:
    def save_draft_change(self, changes: DraftChanges) -> None:
        with self.unit_of_work() as uow:
            uow.drafts.save(changes.drafts)
            uow.transactions.save(changes.transactions)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(transactions=changes.transactions, drafts=changes.drafts)

    def save_recurring_change(self, changes: RecurringChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.recurring.save_items(changes.items)
            uow.drafts.save(changes.drafts)
            uow.accounts.save(accounts)
            self._save_write_metadata(uow, changes.metadata)
        self.update_read_cache(accounts=accounts, drafts=changes.drafts, recurring_items=changes.items)
