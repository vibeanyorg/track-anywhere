from __future__ import annotations

from ..storage_changes import AuthLoginChanges, BookDirectoryChanges, UserChanges


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

    def _commit_auth_login_change(self, *, users=(), identities=()) -> None:
        metadata = self._write_metadata()
        changes = AuthLoginChanges(
            book_changes=self._book_changes(),
            users=tuple(users),
            identities=tuple(identities),
            metadata=metadata,
        )
        self.storage.save_auth_login_change(changes)
        self._mark_metadata_committed(metadata)
        self.books.mark_clean()
