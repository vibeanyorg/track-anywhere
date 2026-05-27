from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from .errors import ValidationError
from .password_auth import PasswordAccount
from .storage_models import PasswordAccountRecord


class StoragePasswordAccountRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def create(self, *, email: str, display_name: str, password_hash: str) -> PasswordAccount:
        with self._session_factory.begin() as session:
            if session.get(PasswordAccountRecord, email) is not None:
                raise ValidationError("email is already registered")
            account = PasswordAccount(
                email=email,
                display_name=display_name,
                password_hash=password_hash,
                role="owner" if _password_account_count(session) == 0 else "viewer",
            )
            session.add(
                PasswordAccountRecord(
                    email=account.email,
                    display_name=account.display_name,
                    password_hash=account.password_hash,
                    role=account.role,
                    version=1,
                )
            )
            return account

    def get(self, email: str) -> PasswordAccount | None:
        with self._session_factory() as session:
            row = session.get(PasswordAccountRecord, email)
            if row is None:
                return None
            return PasswordAccount(
                email=row.email,
                display_name=row.display_name,
                password_hash=row.password_hash,
                role=row.role,
            )


def _password_account_count(session: Session) -> int:
    return int(session.query(func.count(PasswordAccountRecord.email)).scalar() or 0)
