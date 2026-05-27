from __future__ import annotations

from ..storage_changes import StartupMaintenanceChanges


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
