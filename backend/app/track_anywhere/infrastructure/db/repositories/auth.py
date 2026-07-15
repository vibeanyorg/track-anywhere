from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.auth import (
    AuthIdentityRecord,
    BookMemberRecord,
    CredentialRecord,
    OAuthAuthorizationGrantRecord,
    OAuthClientRecord,
    OAuthDeviceGrantRecord,
    UserRecord,
)
from . import RowLock, apply_row_lock


class AuthRecordNotFound(LookupError):
    pass


class CredentialUnavailable(PermissionError):
    pass


class AuthorizationGrantUnavailable(PermissionError):
    pass


class DeviceGrantUnavailable(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class UserSnapshot:
    user_id: str
    subject_type: str
    current_display_name: str
    status: str


@dataclass(frozen=True, slots=True)
class BookMembershipSnapshot:
    book_id: UUID
    user_id: str
    role: str
    status: str
    scopes: tuple[str, ...]
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuthIdentitySnapshot:
    identity_id: UUID
    provider: str
    subject: str
    user_id: str
    email: str | None
    email_verified: bool
    display_name: str | None
    picture_url: str | None
    status: str


@dataclass(frozen=True, slots=True)
class CredentialSnapshot:
    credential_id: UUID
    jti: UUID
    actor_subject_id: str
    actor_type: str
    auth_kind: str
    book_id: UUID | None
    oauth_client_id: str | None
    resource: str | None
    refresh_family_id: UUID | None
    scopes: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class OAuthClientSnapshot:
    client_id: str
    client_name: str
    client_type: str
    scopes: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class AuthorizationGrantSnapshot:
    client_id: str
    redirect_uri: str
    actor_subject_id: str
    scopes: tuple[str, ...]
    code_challenge: str
    challenge_method: str
    resource: str | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeviceGrantSnapshot:
    client_id: str
    scopes: tuple[str, ...]
    resource: str | None
    status: str
    created_at: datetime
    expires_at: datetime
    interval_seconds: int
    last_poll_at: datetime | None
    poll_count: int
    approved_actor_subject_id: str | None
    approved_at: datetime | None
    consumed_at: datetime | None


class BookMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        book_id: UUID,
        user_id: str,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> BookMembershipSnapshot:
        record = self._session.execute(
            apply_row_lock(
                select(BookMemberRecord)
                .where(
                    BookMemberRecord.book_id == book_id,
                    BookMemberRecord.user_id == user_id,
                )
                .execution_options(populate_existing=True),
                lock,
            )
        ).scalar_one_or_none()
        if record is None:
            raise AuthRecordNotFound("book membership not found")
        return self._snapshot(record)

    def get_membership(
        self,
        book_id: UUID,
        user_id: str,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> BookMembershipSnapshot:
        return self.get(book_id, user_id, lock=lock)

    def revoke(
        self,
        book_id: UUID,
        user_id: str,
        *,
        revoked_at: datetime,
    ) -> BookMembershipSnapshot:
        record = self._session.execute(
            select(BookMemberRecord)
            .where(
                BookMemberRecord.book_id == book_id,
                BookMemberRecord.user_id == user_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if record is None:
            raise AuthRecordNotFound("book membership not found")
        if record.status == "active":
            if revoked_at < record.created_at:
                raise ValueError("membership revocation cannot predate creation")
            record.status = "revoked"
            record.revoked_at = revoked_at
            self._session.flush()
        return self._snapshot(record)

    @staticmethod
    def _snapshot(record: BookMemberRecord) -> BookMembershipSnapshot:
        return BookMembershipSnapshot(
            book_id=record.book_id,
            user_id=record.user_id,
            role=record.role,
            status=record.status,
            scopes=tuple(record.scopes),
            revoked_at=record.revoked_at,
        )


class AuthRepository(BookMembershipRepository):
    def get_user(
        self,
        user_id: str,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> UserSnapshot:
        record = self._one(
            apply_row_lock(
                select(UserRecord).where(UserRecord.user_id == user_id),
                lock,
            ),
            "user",
        )
        return UserSnapshot(
            user_id=record.user_id,
            subject_type=record.subject_type,
            current_display_name=record.current_display_name,
            status=record.status,
        )

    def disable_user(self, user_id: str) -> UserSnapshot:
        record = self._one(
            select(UserRecord).where(UserRecord.user_id == user_id).with_for_update(),
            "user",
        )
        if record.status == "active":
            record.status = "disabled"
            self._session.flush()
        return UserSnapshot(
            user_id=record.user_id,
            subject_type=record.subject_type,
            current_display_name=record.current_display_name,
            status=record.status,
        )

    def get_identity(
        self,
        provider: str,
        subject: str,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> AuthIdentitySnapshot:
        record = self._one(
            apply_row_lock(
                select(AuthIdentityRecord).where(
                    AuthIdentityRecord.provider == provider,
                    AuthIdentityRecord.subject == subject,
                ),
                lock,
            ),
            "auth identity",
        )
        return AuthIdentitySnapshot(
            identity_id=record.identity_id,
            provider=record.provider,
            subject=record.subject,
            user_id=record.user_id,
            email=record.email,
            email_verified=record.email_verified,
            display_name=record.display_name,
            picture_url=record.picture_url,
            status=record.status,
        )

    def disable_identity(
        self,
        provider: str,
        subject: str,
    ) -> AuthIdentitySnapshot:
        record = self._one(
            select(AuthIdentityRecord)
            .where(
                AuthIdentityRecord.provider == provider,
                AuthIdentityRecord.subject == subject,
            )
            .with_for_update(),
            "auth identity",
        )
        if record.status == "active":
            record.status = "disabled"
            self._session.flush()
        return AuthIdentitySnapshot(
            identity_id=record.identity_id,
            provider=record.provider,
            subject=record.subject,
            user_id=record.user_id,
            email=record.email,
            email_verified=record.email_verified,
            display_name=record.display_name,
            picture_url=record.picture_url,
            status=record.status,
        )

    def get_credential(
        self,
        token_hash: bytes,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> CredentialSnapshot:
        record = self._credential_record(token_hash, lock=lock)
        return self._credential_snapshot(record)

    def mark_credential_used(
        self,
        token_hash: bytes,
        used_at: datetime | None = None,
    ) -> CredentialSnapshot:
        record = self._credential_record(token_hash, lock=RowLock.UPDATE)
        # Capture the normal request timestamp only after acquiring the row
        # lock. Otherwise two requests can observe t1 < t2 but acquire the lock
        # in the opposite order and incorrectly turn the later request into a
        # 401. Explicit timestamps remain supported for lifecycle tests.
        effective_used_at = datetime.now(UTC) if used_at is None else used_at
        if (
            record.revoked_at is not None
            or record.expires_at <= effective_used_at
            or effective_used_at < record.issued_at
            or (
                record.last_used_at is not None
                and effective_used_at < record.last_used_at
            )
        ):
            raise CredentialUnavailable("credential is revoked or expired")
        record.last_used_at = effective_used_at
        self._session.flush()
        return self._credential_snapshot(record)

    def revoke_credential(
        self,
        token_hash: bytes,
        revoked_at: datetime,
    ) -> CredentialSnapshot:
        record = self._credential_record(token_hash, lock=RowLock.UPDATE)
        if record.revoked_at is None:
            if revoked_at < record.issued_at:
                raise CredentialUnavailable("credential revocation predates issue")
            record.revoked_at = revoked_at
            self._session.flush()
        return self._credential_snapshot(record)

    def get_oauth_client(
        self,
        client_id: str,
        *,
        lock: RowLock = RowLock.NONE,
    ) -> OAuthClientSnapshot:
        record = self._one(
            apply_row_lock(
                select(OAuthClientRecord).where(
                    OAuthClientRecord.client_id == client_id
                ),
                lock,
            ),
            "OAuth client",
        )
        return OAuthClientSnapshot(
            client_id=record.client_id,
            client_name=record.client_name,
            client_type=record.client_type,
            scopes=tuple(record.scopes),
            status=record.status,
        )

    def revoke_authorization_grant(
        self,
        code_hash: bytes,
        *,
        revoked_at: datetime,
    ) -> AuthorizationGrantSnapshot:
        record = self._session.execute(
            select(OAuthAuthorizationGrantRecord)
            .where(OAuthAuthorizationGrantRecord.code_hash == code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.used_at is not None
            or revoked_at < record.created_at
        ):
            raise AuthorizationGrantUnavailable("authorization grant is unavailable")
        if record.revoked_at is None:
            record.revoked_at = revoked_at
            self._session.flush()
        return self._authorization_grant_snapshot(record)

    def consume_authorization_grant(
        self,
        code_hash: bytes,
        *,
        used_at: datetime,
    ) -> AuthorizationGrantSnapshot:
        record = self._session.execute(
            select(OAuthAuthorizationGrantRecord)
            .where(OAuthAuthorizationGrantRecord.code_hash == code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.used_at is not None
            or record.revoked_at is not None
            or record.expires_at <= used_at
            or used_at < record.created_at
        ):
            raise AuthorizationGrantUnavailable("authorization grant is unavailable")
        record.used_at = used_at
        self._session.flush()
        return self._authorization_grant_snapshot(record)

    def approve_device_grant(
        self,
        user_code_hash: bytes,
        *,
        actor_subject_id: str,
        approved_at: datetime,
    ) -> DeviceGrantSnapshot:
        record = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(OAuthDeviceGrantRecord.user_code_hash == user_code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.status != "pending"
            or record.expires_at <= approved_at
            or approved_at < record.created_at
        ):
            raise DeviceGrantUnavailable("device grant cannot be approved")
        record.status = "approved"
        record.approved_actor_subject_id = actor_subject_id
        record.approved_at = approved_at
        self._session.flush()
        return self._device_grant_snapshot(record)

    def consume_device_grant(
        self,
        device_code_hash: bytes,
        *,
        consumed_at: datetime,
    ) -> DeviceGrantSnapshot:
        record = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(OAuthDeviceGrantRecord.device_code_hash == device_code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.status != "approved"
            or record.approved_at is None
            or record.expires_at <= consumed_at
            or consumed_at < record.approved_at
        ):
            raise DeviceGrantUnavailable("device grant cannot be consumed")
        record.status = "consumed"
        record.consumed_at = consumed_at
        self._session.flush()
        return self._device_grant_snapshot(record)

    def record_device_poll(
        self,
        device_code_hash: bytes,
        *,
        polled_at: datetime,
    ) -> DeviceGrantSnapshot:
        record = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(OAuthDeviceGrantRecord.device_code_hash == device_code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.status not in {"pending", "approved"}
            or record.expires_at <= polled_at
            or polled_at < record.created_at
            or (record.last_poll_at is not None and polled_at < record.last_poll_at)
        ):
            raise DeviceGrantUnavailable("device grant cannot be polled")
        record.last_poll_at = polled_at
        record.poll_count += 1
        self._session.flush()
        return self._device_grant_snapshot(record)

    def deny_device_grant(
        self,
        user_code_hash: bytes,
        *,
        denied_at: datetime,
    ) -> DeviceGrantSnapshot:
        record = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(OAuthDeviceGrantRecord.user_code_hash == user_code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.status != "pending"
            or denied_at < record.created_at
            or record.expires_at <= denied_at
        ):
            raise DeviceGrantUnavailable("device grant cannot be denied")
        record.status = "denied"
        self._session.flush()
        return self._device_grant_snapshot(record)

    def expire_device_grant(
        self,
        device_code_hash: bytes,
        *,
        observed_at: datetime,
    ) -> DeviceGrantSnapshot:
        record = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(OAuthDeviceGrantRecord.device_code_hash == device_code_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.status not in {"pending", "approved"}
            or observed_at < record.expires_at
        ):
            raise DeviceGrantUnavailable("device grant cannot be expired")
        record.status = "expired"
        self._session.flush()
        return self._device_grant_snapshot(record)

    def _credential_record(
        self,
        token_hash: bytes,
        *,
        lock: RowLock,
    ) -> CredentialRecord:
        return self._one(
            apply_row_lock(
                select(CredentialRecord).where(
                    CredentialRecord.token_hash == token_hash
                ),
                lock,
            ),
            "credential",
        )

    def _one(self, statement, entity_name: str):
        record = self._session.execute(statement).scalar_one_or_none()
        if record is None:
            raise AuthRecordNotFound(f"{entity_name} not found")
        return record

    @staticmethod
    def _credential_snapshot(record: CredentialRecord) -> CredentialSnapshot:
        return CredentialSnapshot(
            credential_id=record.credential_id,
            jti=record.jti,
            actor_subject_id=record.actor_subject_id,
            actor_type=record.actor_type,
            auth_kind=record.auth_kind,
            book_id=record.book_id,
            oauth_client_id=record.oauth_client_id,
            resource=record.resource,
            refresh_family_id=record.refresh_family_id,
            scopes=tuple(record.scopes),
            issued_at=record.issued_at,
            expires_at=record.expires_at,
            revoked_at=record.revoked_at,
            last_used_at=record.last_used_at,
        )

    @staticmethod
    def _authorization_grant_snapshot(
        record: OAuthAuthorizationGrantRecord,
    ) -> AuthorizationGrantSnapshot:
        return AuthorizationGrantSnapshot(
            client_id=record.client_id,
            redirect_uri=record.redirect_uri,
            actor_subject_id=record.actor_subject_id,
            scopes=tuple(record.scopes),
            code_challenge=record.code_challenge,
            challenge_method=record.challenge_method,
            resource=record.resource,
            created_at=record.created_at,
            expires_at=record.expires_at,
            used_at=record.used_at,
            revoked_at=record.revoked_at,
        )

    @staticmethod
    def _device_grant_snapshot(record: OAuthDeviceGrantRecord) -> DeviceGrantSnapshot:
        return DeviceGrantSnapshot(
            client_id=record.client_id,
            scopes=tuple(record.scopes),
            resource=record.resource,
            status=record.status,
            created_at=record.created_at,
            expires_at=record.expires_at,
            interval_seconds=record.interval_seconds,
            last_poll_at=record.last_poll_at,
            poll_count=record.poll_count,
            approved_actor_subject_id=record.approved_actor_subject_id,
            approved_at=record.approved_at,
            consumed_at=record.consumed_at,
        )


__all__ = [
    "AuthIdentitySnapshot",
    "AuthRecordNotFound",
    "AuthRepository",
    "AuthorizationGrantSnapshot",
    "AuthorizationGrantUnavailable",
    "BookMembershipRepository",
    "BookMembershipSnapshot",
    "CredentialSnapshot",
    "CredentialUnavailable",
    "DeviceGrantSnapshot",
    "DeviceGrantUnavailable",
    "OAuthClientSnapshot",
    "UserSnapshot",
]
