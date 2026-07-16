from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.auth import PasswordAccountRecord, UserRecord
from ..infrastructure.db.repositories.auth import AuthRepository
from .contracts import OWNER_SCOPES, PasswordSessionCommand, PasswordSignupCommand
from .errors import AuthPolicyDenied
from .security import hash_password, verify_password_hash
from .sessions import IssuedBrowserSession, PersistentSessionService


_ACCOUNT_SETUP_LOCK_ID = 6072359372318659668
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$390000$invalid-password-saltxxx$"
    "38626767733309d3de22ae2a530ded40126030386c23194ff21b83bb5faae2c3"
)


class AccountSetupComplete(AuthPolicyDenied):
    pass


class PersistentPasswordService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def signup(self, command: PasswordSignupCommand) -> IssuedBrowserSession:
        session_service = PersistentSessionService(self._session)
        setup_credential, setup_user = session_service.authenticate_credential(
            command.setup_key,
            allowed_auth_kinds=frozenset({"api_key"}),
        )
        if (
            setup_user.subject_type != "human"
            or setup_user.status != "active"
            or setup_credential.book_id is not None
            or not OWNER_SCOPES.issubset(setup_credential.scopes)
        ):
            raise AuthPolicyDenied("setup credential subject is unavailable")

        self._session.execute(
            select(func.pg_advisory_xact_lock(_ACCOUNT_SETUP_LOCK_ID))
        ).scalar_one()
        existing = self._session.scalar(select(PasswordAccountRecord.user_id).limit(1))
        if existing is not None:
            raise AccountSetupComplete("account setup is already complete")

        user = self._session.scalar(
            select(UserRecord)
            .where(UserRecord.user_id == setup_user.user_id)
            .with_for_update()
        )
        if user is None or user.subject_type != "human" or user.status != "active":
            raise AuthPolicyDenied("setup credential subject is unavailable")
        user.current_display_name = command.display_name
        user.updated_at = datetime.now(UTC)

        self._session.add(
            PasswordAccountRecord(
                user_id=user.user_id,
                normalized_email=command.email,
                password_hash=hash_password(command.password),
                status="active",
            )
        )
        self._session.flush()
        snapshot = AuthRepository(self._session).get_user(user.user_id)
        return session_service.issue_password_session(user=snapshot)

    def login(self, command: PasswordSessionCommand) -> IssuedBrowserSession:
        row = self._session.execute(
            select(PasswordAccountRecord, UserRecord)
            .join(UserRecord, PasswordAccountRecord.user_id == UserRecord.user_id)
            .where(PasswordAccountRecord.normalized_email == command.email)
        ).one_or_none()
        encoded = _DUMMY_PASSWORD_HASH if row is None else row[0].password_hash
        password_matches = verify_password_hash(command.password, encoded)
        if row is None:
            raise AuthPolicyDenied("email or password is invalid")
        password_account, user = row
        if (
            not password_matches
            or password_account.status != "active"
            or user.status != "active"
            or user.subject_type != "human"
        ):
            raise AuthPolicyDenied("email or password is invalid")
        snapshot = AuthRepository(self._session).get_user(user.user_id)
        return PersistentSessionService(self._session).issue_password_session(
            user=snapshot
        )


__all__ = [
    "AccountSetupComplete",
    "PersistentPasswordService",
]
