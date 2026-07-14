from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.auth import (
    BrowserSessionRecord,
    CredentialRecord,
    UserRecord,
)
from ..infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    AuthRepository,
    CredentialSnapshot,
    CredentialUnavailable,
    UserSnapshot,
)
from .errors import AuthPolicyDenied
from .security import new_secret, secret_digest


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    user_id: str
    display_name: str
    subject_type: str
    auth_kind: str
    book_id: str | None
    scopes: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "subject_type": self.subject_type,
            "auth_kind": self.auth_kind,
            "book_id": self.book_id,
            "scopes": list(self.scopes),
        }


@dataclass(frozen=True, slots=True)
class IssuedBrowserSession:
    session_token: str
    csrf_token: str
    identity: SessionIdentity


@dataclass(frozen=True, slots=True)
class ActiveBrowserSession:
    record: BrowserSessionRecord
    credential: CredentialRecord
    user: UserRecord

    @property
    def identity(self) -> SessionIdentity:
        return _identity(self.user, self.credential)


class PersistentSessionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def issue_from_api_key(self, raw_token: str) -> IssuedBrowserSession:
        credential, user = self.authenticate_credential(raw_token)
        return self.issue_browser_session(credential=credential, user=user)

    def authenticate_credential(
        self,
        raw_token: str,
    ) -> tuple[CredentialSnapshot, UserSnapshot]:
        observed_at = datetime.now(UTC)
        repository = AuthRepository(self._session)
        try:
            credential = repository.mark_credential_used(
                secret_digest(raw_token),
                observed_at,
            )
            user = repository.get_user(credential.actor_subject_id)
        except (AuthRecordNotFound, CredentialUnavailable, ValueError) as error:
            raise AuthPolicyDenied(
                "credential is missing, expired, or revoked"
            ) from error
        if user.status != "active":
            raise AuthPolicyDenied("credential subject is disabled")
        return credential, user

    def issue_browser_session(
        self,
        *,
        credential: CredentialSnapshot,
        user: UserSnapshot,
        ttl: timedelta = timedelta(hours=8),
    ) -> IssuedBrowserSession:
        now = datetime.now(UTC)
        expires_at = min(now + ttl, credential.expires_at)
        browser_credential_hash = secret_digest(new_secret("ta"))
        session_token = new_secret("sess")
        csrf_token = new_secret("csrf")
        browser_credential = CredentialRecord(
            credential_id=uuid4(),
            token_hash=browser_credential_hash,
            jti=uuid4(),
            actor_subject_id=user.user_id,
            actor_type=user.subject_type,
            auth_kind="browser_session",
            book_id=credential.book_id,
            scopes=list(credential.scopes),
            issued_at=now,
            expires_at=expires_at,
            revoked_at=None,
            last_used_at=now,
        )
        self._session.add(browser_credential)
        self._session.flush([browser_credential])
        self._session.add(
            BrowserSessionRecord(
                session_hash=secret_digest(session_token),
                csrf_token_hash=secret_digest(csrf_token),
                credential_hash=browser_credential_hash,
                user_id=user.user_id,
                issued_at=now,
                expires_at=expires_at,
                revoked_at=None,
                last_seen_at=now,
            )
        )
        self._session.flush()
        return IssuedBrowserSession(
            session_token=session_token,
            csrf_token=csrf_token,
            identity=SessionIdentity(
                user_id=user.user_id,
                display_name=user.current_display_name,
                subject_type=user.subject_type,
                auth_kind="browser_session",
                book_id=None if credential.book_id is None else str(credential.book_id),
                scopes=credential.scopes,
            ),
        )

    def current(self, raw_session: str | None, *, lock: bool = False) -> ActiveBrowserSession | None:
        if not raw_session:
            return None
        statement = (
            select(BrowserSessionRecord, CredentialRecord, UserRecord)
            .join(
                CredentialRecord,
                BrowserSessionRecord.credential_hash == CredentialRecord.token_hash,
            )
            .join(UserRecord, BrowserSessionRecord.user_id == UserRecord.user_id)
            .where(BrowserSessionRecord.session_hash == secret_digest(raw_session))
        )
        if lock:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        browser, credential, user = row
        now = datetime.now(UTC)
        if (
            browser.revoked_at is not None
            or browser.expires_at <= now
            or credential.revoked_at is not None
            or credential.expires_at <= now
            or user.status != "active"
        ):
            return None
        browser.last_seen_at = now
        return ActiveBrowserSession(browser, credential, user)

    def verify_csrf(self, active: ActiveBrowserSession, csrf_token: str | None) -> bool:
        return bool(
            csrf_token
            and active.record.csrf_token_hash == secret_digest(csrf_token)
        )

    def revoke(self, raw_session: str | None) -> None:
        active = self.current(raw_session, lock=True)
        if active is not None and active.record.revoked_at is None:
            active.record.revoked_at = datetime.now(UTC)

def _identity(user: UserRecord, credential: CredentialRecord) -> SessionIdentity:
    return SessionIdentity(
        user_id=user.user_id,
        display_name=user.current_display_name,
        subject_type=user.subject_type,
        auth_kind=credential.auth_kind,
        book_id=None if credential.book_id is None else str(credential.book_id),
        scopes=tuple(credential.scopes),
    )


__all__ = [
    "ActiveBrowserSession",
    "IssuedBrowserSession",
    "PersistentSessionService",
    "SessionIdentity",
]
