from __future__ import annotations

from .audit import AuditEvent
from .auth_identities import LinkedAuthIdentity
from .domain_storage_models import BookMemberRecord, LedgerBookRecord
from .oauth_grants import AuthorizationGrant, DeviceGrant
from .security import Actor
from .storage_auth_models import CredentialRecord
from .storage_auth_models import OAuthAuthorizationGrantRecord, OAuthDeviceGrantRecord
from .storage_json import to_jsonable
from .storage_models import AuthIdentityRecord, UserRecord
from .users import AppUser


class AuthStorageWriters:
    def _save_credentials(self, session, credentials) -> None:
        for credential in credentials:
            self._upsert_record(
                session,
                CredentialRecord,
                {
                    "token_hash": credential.token_hash,
                    "actor_id": credential.actor.actor_id,
                    "actor_type": credential.actor.actor_type,
                    "scopes": sorted(credential.actor.scopes),
                    "issued_at": credential.issued_at.isoformat(),
                    "expires_at": credential.expires_at.isoformat(),
                    "jti": credential.jti,
                    "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
                    "auth_kind": credential.auth_kind,
                    "name": credential.name,
                    "description": credential.description,
                    "key_prefix": credential.key_prefix,
                    "created_by_actor_id": credential.created_by_actor_id,
                    "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
                    "rotated_from_jti": credential.rotated_from_jti,
                },
                ["token_hash"],
            )

    def save_credential(self, credential) -> None:
        with self.session_factory.begin() as session:
            self._save_credentials(session, [credential])

    def save_credential_and_audit_event(self, credential, audit_event: AuditEvent) -> None:
        with self.session_factory.begin() as session:
            self._save_credentials(session, [credential])
            self._save_audit_events(session, [audit_event])

    def save_credential_issue_state(self, *, book, member, credentials, audit_event: AuditEvent) -> None:
        with self.session_factory.begin() as session:
            session.merge(
                LedgerBookRecord(
                    book_id=book.book_id,
                    name=book.name,
                    kind=book.kind,
                    base_currency=book.base_currency,
                    timezone=book.timezone,
                    status=book.status,
                    template_key=book.template_key,
                    settings=to_jsonable(book.settings),
                    created_by=book.created_by,
                    version=book.version,
                )
            )
            session.merge(
                BookMemberRecord(
                    book_id=member.book_id,
                    user_id=member.user_id,
                    role=member.role,
                    status=member.status,
                    scopes=list(member.scopes),
                    version=member.version,
                )
            )
            self._save_credentials(session, credentials)
            self._save_audit_events(session, [audit_event])

    def save_authorization_grant(self, grant: AuthorizationGrant) -> None:
        with self.session_factory.begin() as session:
            session.merge(
                OAuthAuthorizationGrantRecord(
                    code_hash=grant.code_hash,
                    client_id=grant.client_id,
                    redirect_uri=grant.redirect_uri,
                    actor_id=grant.actor.actor_id,
                    actor_type=grant.actor.actor_type,
                    actor_scopes=sorted(grant.actor.scopes),
                    scopes=list(grant.scopes),
                    code_challenge=grant.code_challenge,
                    resource=grant.resource,
                    expires_at=grant.expires_at.isoformat(),
                    used=grant.used,
                )
            )

    def load_authorization_grant(self, code_hash: str) -> AuthorizationGrant | None:
        with self.session_factory() as session:
            row = session.get(OAuthAuthorizationGrantRecord, code_hash)
            return _authorization_grant(row) if row is not None else None

    def save_device_grant(self, grant: DeviceGrant) -> None:
        with self.session_factory.begin() as session:
            actor = grant.approved_actor
            session.merge(
                OAuthDeviceGrantRecord(
                    device_code_hash=grant.device_code_hash,
                    user_code_hash=grant.user_code_hash,
                    client_id=grant.client_id,
                    scopes=list(grant.scopes),
                    resource=grant.resource,
                    status=grant.status,
                    expires_at=grant.expires_at.isoformat(),
                    interval_seconds=grant.interval_seconds,
                    created_at=grant.created_at.isoformat(),
                    last_poll_at=grant.last_poll_at.isoformat() if grant.last_poll_at else None,
                    poll_count=grant.poll_count,
                    approved_actor_id=actor.actor_id if actor else None,
                    approved_actor_type=actor.actor_type if actor else None,
                    approved_actor_scopes=sorted(actor.scopes) if actor else None,
                    approved_at=grant.approved_at.isoformat() if grant.approved_at else None,
                )
            )

    def load_device_grant_by_device_hash(self, device_code_hash: str) -> DeviceGrant | None:
        with self.session_factory() as session:
            row = session.get(OAuthDeviceGrantRecord, device_code_hash)
            return _device_grant(row) if row is not None else None

    def load_device_grant_by_user_hash(self, user_code_hash: str) -> DeviceGrant | None:
        with self.session_factory() as session:
            row = session.query(OAuthDeviceGrantRecord).filter_by(user_code_hash=user_code_hash).first()
            return _device_grant(row) if row is not None else None

    def save_auth_login_state(
        self,
        *,
        book,
        members,
        user: AppUser,
        identity: LinkedAuthIdentity,
        credential,
        audit_event: AuditEvent,
    ) -> None:
        with self.session_factory.begin() as session:
            session.merge(
                LedgerBookRecord(
                    book_id=book.book_id,
                    name=book.name,
                    kind=book.kind,
                    base_currency=book.base_currency,
                    timezone=book.timezone,
                    status=book.status,
                    template_key=book.template_key,
                    settings=to_jsonable(book.settings),
                    created_by=book.created_by,
                    version=book.version,
                )
            )
            seen_members = set()
            for member in members:
                member_key = (member.book_id, member.user_id)
                if member_key in seen_members:
                    continue
                seen_members.add(member_key)
                session.merge(
                    BookMemberRecord(
                        book_id=member.book_id,
                        user_id=member.user_id,
                        role=member.role,
                        status=member.status,
                        scopes=list(member.scopes),
                        version=member.version,
                    )
                )
            session.merge(
                UserRecord(
                    user_id=user.user_id,
                    username=user.username,
                    display_name=user.display_name,
                    version=user.version,
                )
            )
            session.merge(
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
            self._save_credentials(session, [credential])
            self._save_audit_events(session, [audit_event])


def _authorization_grant(row) -> AuthorizationGrant:
    from datetime import datetime
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
    from datetime import datetime
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
