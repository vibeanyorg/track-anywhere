from __future__ import annotations

from ..storage_changes import LedgerChanges, ReclassificationChanges


class ServiceLedgerPersistence:
    def _commit_ledger_change(self, *transactions, accounts=(), include_category_history: bool = False) -> None:
        metadata = self._write_metadata()
        changes = LedgerChanges(
            transactions=tuple(transactions),
            metadata=metadata,
            accounts=tuple(accounts),
            assets=tuple(self.assets.dirty_assets()),
            adjustment_account_ids=dict(self.adjustment_account_ids),
            category_history=self._category_history_changes() if include_category_history else None,
            counterparties=tuple(self.counterparties.dirty_counterparties()),
        )
        self.storage.save_ledger_change(changes)
        self._mark_metadata_committed(metadata)
        self.assets.mark_clean()
        self.counterparties.mark_clean()
        if include_category_history:
            self.categories.mark_clean()

    def _commit_reclassification_change(self, transaction, line_id: str) -> None:
        metadata = self._write_metadata()
        changes = ReclassificationChanges(
            transaction=transaction,
            line_id=line_id,
            category_history=self._category_history_changes(),
            metadata=metadata,
        )
        self.storage.save_reclassification_change(changes)
        self._mark_metadata_committed(metadata)
        self.categories.mark_clean()
