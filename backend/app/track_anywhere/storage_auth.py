from __future__ import annotations

from .storage_auth_models import CredentialRecord
from .storage_auth_models import OAuthAuthorizationGrantRecord, OAuthDeviceGrantRecord
from .storage_upsert_writers import upsert_record


def save_credentials(session, credentials) -> None:
    for credential in credentials:
        upsert_record(
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


def save_authorization_grants(session, grants) -> None:
    for grant in grants:
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


def save_device_grants(session, grants) -> None:
    for grant in grants:
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
