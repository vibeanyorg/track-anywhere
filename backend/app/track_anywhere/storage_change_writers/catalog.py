from __future__ import annotations

from ..storage_changes import CatalogChanges


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
