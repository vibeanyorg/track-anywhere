from __future__ import annotations

from typing import Any, Iterable

from ..storage_models import AuthIdentityRecord, UserRecord


class UserRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, users: Iterable[Any]) -> None:
        for user in users:
            self.session.merge(
                UserRecord(
                    user_id=user.user_id,
                    username=user.username,
                    display_name=user.display_name,
                    version=user.version,
                )
            )


class AuthIdentityRepository:
    def __init__(self, session) -> None:
        self.session = session

    def save(self, identities: Iterable[Any]) -> None:
        for identity in identities:
            self.session.merge(
                AuthIdentityRecord(
                    identity_id=identity.identity_id,
                    provider=identity.provider,
                    subject=identity.subject,
                    user_id=identity.user_id,
                    email=identity.email,
                    email_verified=identity.email_verified,
                    display_name=identity.display_name,
                    picture_url=identity.picture_url,
                    status=identity.status,
                    version=identity.version,
                )
            )


class AuditRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        self.storage._save_audit_events(self.session, list(events))


class CredentialRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save(self, credentials: Iterable[Any]) -> None:
        self.storage._save_credentials(self.session, credentials)


class IdempotencyRepository:
    def __init__(self, storage, session) -> None:
        self.storage = storage
        self.session = session

    def save_receipts(self, receipts: Iterable[Any]) -> None:
        self.storage._save_idempotency_receipts(self.session, receipts)
