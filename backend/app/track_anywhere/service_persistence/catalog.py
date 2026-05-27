from __future__ import annotations

from ..storage_changes import CatalogChanges


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
