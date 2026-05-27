from __future__ import annotations

from ..storage_changes import LedgerChanges


class LedgerChangeStorageWriters:
    def save_ledger_change(self, changes: LedgerChanges) -> None:
        accounts = list(changes.accounts)
        with self.unit_of_work() as uow:
            uow.assets.save(changes.assets)
            uow.accounts.save(accounts)
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
