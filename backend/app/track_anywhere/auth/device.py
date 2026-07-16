from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.auth import (
    OAuthClientRecord,
    OAuthDeviceGrantRecord,
)
from .contracts import (
    DeviceApprovalCommand,
    OAuthDeviceAuthorizeCommand,
    OAuthDeviceTokenCommand,
)
from .errors import AuthPolicyDenied, AuthSecurityError, OAuthFlowError
from .oauth import PersistentOAuthService, require_resource_scope_floor
from .resources import configured_public_base_url, require_oauth_resource
from .security import (
    new_secret,
    new_user_code,
    normalize_user_code,
    parse_requested_scopes,
    require_scope_subset,
    secret_digest,
)
from .sessions import ActiveBrowserSession


class PersistentDeviceService:
    def __init__(self, session: Session, public_base_url: str | None = None) -> None:
        self._session = session
        self._public_base_url = public_base_url or configured_public_base_url()

    def create_authorization(
        self,
        command: OAuthDeviceAuthorizeCommand,
        issuer: str,
    ) -> dict[str, object]:
        client = self._active_client(command.client_id)
        require_oauth_resource(command.resource, self._public_base_url)
        scopes = parse_requested_scopes(command.scope)
        require_scope_subset(scopes, set(client.scopes))
        raw_device_code = new_secret("dev")
        raw_user_code = new_user_code()
        now = datetime.now(UTC)
        expires_in = 900
        interval = 5
        self._session.add(
            OAuthDeviceGrantRecord(
                device_code_hash=secret_digest(raw_device_code),
                user_code_hash=secret_digest(normalize_user_code(raw_user_code)),
                client_id=client.client_id,
                scopes=list(scopes),
                resource=command.resource,
                status="pending",
                created_at=now,
                expires_at=now + timedelta(seconds=expires_in),
                interval_seconds=interval,
                last_poll_at=None,
                poll_count=0,
                approved_actor_subject_id=None,
                approved_at=None,
                consumed_at=None,
            )
        )
        self._session.flush()
        verification_uri = f"{issuer.rstrip('/')}/auth/device"
        return {
            "device_code": raw_device_code,
            "user_code": raw_user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": (
                f"{verification_uri}?user_code={raw_user_code}"
            ),
            "expires_in": expires_in,
            "interval": interval,
        }

    def approve(
        self,
        command: DeviceApprovalCommand,
        active: ActiveBrowserSession,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        grant = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(
                OAuthDeviceGrantRecord.user_code_hash
                == secret_digest(normalize_user_code(command.user_code))
            )
            .with_for_update()
        ).scalar_one_or_none()
        if grant is None or grant.status != "pending" or grant.expires_at <= now:
            raise AuthPolicyDenied("device code is invalid or expired")
        if command.action == "deny":
            grant.status = "denied"
            self._session.flush()
            return {"status": "denied"}

        if active.credential.book_id is not None:
            raise AuthPolicyDenied(
                "book-bound credentials cannot approve OAuth access"
            )

        scopes = tuple(grant.scopes)
        if command.approved_scopes is not None:
            scopes = parse_requested_scopes(" ".join(command.approved_scopes))
            require_scope_subset(scopes, set(grant.scopes))
        require_scope_subset(scopes, set(active.credential.scopes))
        require_resource_scope_floor(grant.resource, scopes)
        grant.scopes = list(scopes)
        grant.status = "approved"
        grant.approved_actor_subject_id = active.user.user_id
        grant.approved_at = now
        self._session.flush()
        return {"status": "approved", "scope": " ".join(scopes)}

    def exchange(self, command: OAuthDeviceTokenCommand) -> dict[str, object]:
        now = datetime.now(UTC)
        grant = self._session.execute(
            select(OAuthDeviceGrantRecord)
            .where(
                OAuthDeviceGrantRecord.device_code_hash
                == secret_digest(command.device_code)
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            grant is None
            or grant.client_id != command.client_id
            or grant.resource != command.resource
        ):
            raise OAuthFlowError("invalid_grant", "device code is invalid")
        if grant.expires_at <= now:
            if grant.status in {"pending", "approved"}:
                grant.status = "expired"
                self._session.flush()
            raise OAuthFlowError("expired_token", "device code expired")
        if grant.status == "denied":
            raise OAuthFlowError(
                "access_denied", "device authorization was denied"
            )
        if grant.status not in {"pending", "approved"}:
            raise OAuthFlowError("invalid_grant", "device code is no longer valid")

        if (
            grant.last_poll_at is not None
            and (now - grant.last_poll_at).total_seconds() < grant.interval_seconds
        ):
            grant.interval_seconds += 5
            self._record_poll(grant, now)
            raise OAuthFlowError(
                "slow_down",
                "polling too quickly",
                {"interval": grant.interval_seconds},
            )
        self._record_poll(grant, now)
        if grant.status == "pending":
            raise OAuthFlowError(
                "authorization_pending", "authorization is still pending"
            )
        if grant.approved_actor_subject_id is None or grant.approved_at is None:
            raise OAuthFlowError("invalid_grant", "device approval is incomplete")

        grant.status = "consumed"
        grant.consumed_at = now
        body = PersistentOAuthService(
            self._session,
            self._public_base_url,
        ).issue_token_pair(
            actor_subject_id=grant.approved_actor_subject_id,
            scopes=tuple(grant.scopes),
            auth_kind="device",
            client_id=grant.client_id,
            resource=command.resource,
            issued_at=now,
        )
        self._session.flush()
        return body

    def _active_client(self, client_id: str) -> OAuthClientRecord:
        client = self._session.get(OAuthClientRecord, client_id)
        if client is None or client.status != "active":
            raise AuthSecurityError("unknown OAuth client")
        return client

    def _record_poll(
        self,
        grant: OAuthDeviceGrantRecord,
        now: datetime,
    ) -> None:
        grant.last_poll_at = now
        grant.poll_count += 1
        self._session.flush()


__all__ = ["PersistentDeviceService"]
