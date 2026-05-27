from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from ..oauth_grants import AuthorizationGrant, DeviceGrant
from ..security import Actor
from ..storage_audit_idempotency_writers import save_audit_events, save_idempotency_receipts
from ..storage_auth import save_authorization_grants, save_credentials, save_device_grants
from ..storage_auth_models import OAuthAuthorizationGrantRecord, OAuthDeviceGrantRecord
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
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save_events(self, events: Iterable[Any]) -> None:
        save_audit_events(self.session, list(events))


class CredentialRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save(self, credentials: Iterable[Any]) -> None:
        save_credentials(self.session, credentials)


class IdempotencyRepository:
    def __init__(self, _storage, session) -> None:
        self.session = session

    def save_receipts(self, receipts: Iterable[Any]) -> None:
        save_idempotency_receipts(self.session, receipts)


class PlatformGrantRepository:
    def __init__(self, session) -> None:
        self.session = session

    def load_authorization_grant(self, code_hash: str) -> AuthorizationGrant | None:
        row = self.session.get(OAuthAuthorizationGrantRecord, code_hash)
        return _authorization_grant(row) if row is not None else None

    def load_device_grant_by_device_hash(self, device_code_hash: str) -> DeviceGrant | None:
        row = self.session.get(OAuthDeviceGrantRecord, device_code_hash)
        return _device_grant(row) if row is not None else None

    def load_device_grant_by_user_hash(self, user_code_hash: str) -> DeviceGrant | None:
        row = self.session.query(OAuthDeviceGrantRecord).filter_by(user_code_hash=user_code_hash).first()
        return _device_grant(row) if row is not None else None

    def save_authorization_grants(self, grants: Iterable[Any]) -> None:
        save_authorization_grants(self.session, grants)

    def save_device_grants(self, grants: Iterable[Any]) -> None:
        save_device_grants(self.session, grants)


def _authorization_grant(row) -> AuthorizationGrant:
    return AuthorizationGrant(
        code_hash=row.code_hash,
        client_id=row.client_id,
        redirect_uri=row.redirect_uri,
        actor=Actor(row.actor_id, row.actor_type, frozenset(row.actor_scopes)),
        scopes=tuple(row.scopes),
        code_challenge=row.code_challenge,
        resource=row.resource,
        expires_at=datetime.fromisoformat(row.expires_at),
        used=row.used,
    )


def _device_grant(row) -> DeviceGrant:
    actor = None
    if row.approved_actor_id and row.approved_actor_type and row.approved_actor_scopes is not None:
        actor = Actor(row.approved_actor_id, row.approved_actor_type, frozenset(row.approved_actor_scopes))
    return DeviceGrant(
        device_code_hash=row.device_code_hash,
        user_code_hash=row.user_code_hash,
        client_id=row.client_id,
        scopes=tuple(row.scopes),
        resource=row.resource,
        status=row.status,
        expires_at=datetime.fromisoformat(row.expires_at),
        interval_seconds=row.interval_seconds,
        created_at=datetime.fromisoformat(row.created_at),
        last_poll_at=datetime.fromisoformat(row.last_poll_at) if row.last_poll_at else None,
        poll_count=row.poll_count,
        approved_actor=actor,
        approved_at=datetime.fromisoformat(row.approved_at) if row.approved_at else None,
    )
