from __future__ import annotations

from .password_auth import PasswordAccountStore


class PasswordAuthUseCases:
    def create_password_account_store(self) -> PasswordAccountStore:
        return PasswordAccountStore(self.storage.password_account_repository())
