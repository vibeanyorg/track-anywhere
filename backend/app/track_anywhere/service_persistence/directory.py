from __future__ import annotations

from ..storage_changes import BookDirectoryChanges, UserChanges


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
