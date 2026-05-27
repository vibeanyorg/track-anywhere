from __future__ import annotations

from ..storage_changes import AuthLoginChanges, BookDirectoryChanges, UserChanges


class DirectoryChangeStorageWriters:
    def save_user_change(self, changes: UserChanges) -> None:
        with self.unit_of_work() as uow:
            uow.users.save(changes.users)
            self._save_write_metadata(uow, changes.metadata)

    def save_book_change(self, changes: BookDirectoryChanges) -> None:
        with self.unit_of_work() as uow:
            uow.books.save(changes.book_changes.books, changes.book_changes.members)
            self._save_write_metadata(uow, changes.metadata)

    def save_auth_login_change(self, changes: AuthLoginChanges) -> None:
        with self.unit_of_work() as uow:
            uow.books.save(changes.book_changes.books, changes.book_changes.members)
            uow.users.save(changes.users)
            uow.identities.save(changes.identities)
            self._save_write_metadata(uow, changes.metadata)
