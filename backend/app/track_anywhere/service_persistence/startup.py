from __future__ import annotations

from ..storage_changes import StartupMaintenanceChanges


class ServiceStartupPersistence:
    def _commit_startup_maintenance(self) -> None:
        metadata = self._write_metadata()
        changes = StartupMaintenanceChanges(
            book_changes=self._book_changes(),
            assets=tuple(self.assets.dirty_assets()),
            categories=tuple(self.categories.dirty_categories()),
            category_history=self._category_history_changes(),
            metadata=metadata,
        )
        self.storage.save_startup_maintenance(changes)
        self._mark_metadata_committed(metadata)
        self.books.mark_clean()
        self.assets.mark_clean()
        self.categories.mark_clean()
