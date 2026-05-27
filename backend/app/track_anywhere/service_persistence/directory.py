from __future__ import annotations

from ..storage_changes import BookDirectoryChanges, UserChanges


class ServiceDirectoryPersistence:
    def _commit_user_change(self, *users) -> None:
        metadata = self._write_metadata()
        self.storage.save_user_change(UserChanges(users=tuple(users), metadata=metadata))
        self._mark_metadata_committed(metadata)

    def _commit_book_change(self) -> None:
        metadata = self._write_metadata()
        changes = BookDirectoryChanges(book_changes=self._book_changes(), metadata=metadata)
        self.storage.save_book_change(changes)
        self._mark_metadata_committed(metadata)
        self.books.mark_clean()
