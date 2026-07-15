from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..infrastructure.db.models.auth import (
    CredentialRecord,
    OAuthAuthorizationGrantRecord,
    OAuthClientRecord,
    OAuthClientRedirectUriRecord,
    UserRecord,
)
from ..infrastructure.db.repositories.auth import (
    AuthRecordNotFound,
    AuthRepository,
    CredentialUnavailable,
)
from .contracts import (
    OAuthAuthorizationCodeTokenCommand,
    OAuthAuthorizeCommand,
    OAuthRefreshTokenCommand,
    OAuthRegisterCommand,
)
from .errors import AuthPolicyDenied, AuthSecurityError, OAuthFlowError
from .resources import configured_public_base_url, require_oauth_resource
from .security import (
    new_secret,
    parse_requested_scopes,
    pkce_challenge,
    redirect_uri_matches,
    redirect_with_params,
    require_scope_subset,
    secret_digest,
    validate_redirect_uri,
)
from .sessions import ActiveBrowserSession


OAUTH_ACCESS_KINDS = frozenset({"pkce", "device", "oauth_refresh"})
REFRESH_TOKEN_TTL = timedelta(days=30)
ACCESS_TOKEN_TTL = timedelta(hours=1)


class PersistentOAuthService:
    def __init__(self, session: Session, public_base_url: str | None = None) -> None:
        self._session = session
        self._public_base_url = public_base_url or configured_public_base_url()

    def register_client(self, command: OAuthRegisterCommand) -> dict[str, object]:
        scopes = parse_requested_scopes(command.scope)
        redirects = tuple(
            dict.fromkeys(validate_redirect_uri(uri) for uri in command.redirect_uris)
        )
        client_id = new_secret("client")
        client = OAuthClientRecord(
            client_id=client_id,
            client_name=command.client_name.strip(),
            client_type="public",
            client_secret_hash=None,
            scopes=list(scopes),
            status="active",
        )
        self._session.add(client)
        self._session.flush([client])
        self._session.add_all(
            OAuthClientRedirectUriRecord(
                client_id=client_id,
                redirect_uri=uri,
                status="active",
            )
            for uri in redirects
        )
        self._session.flush()
        return {
            "client_id": client_id,
            "client_name": client.client_name,
            "redirect_uris": list(redirects),
            "grant_types": list(command.grant_types),
            "response_types": list(command.response_types),
            "scope": " ".join(scopes),
            "token_endpoint_auth_method": "none",
        }

    def validate_authorization_request(
        self,
        command: OAuthAuthorizeCommand,
    ) -> dict[str, object]:
        resource = require_oauth_resource(command.resource, self._public_base_url)
        client = self._active_client(command.client_id)
        registered_redirect_uri = self._require_redirect(
            client.client_id,
            command.redirect_uri,
        )
        scopes = parse_requested_scopes(command.scope)
        require_scope_subset(scopes, set(client.scopes))
        return {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uri": command.redirect_uri,
            "registered_redirect_uri": registered_redirect_uri,
            "resource": resource,
            "scopes": list(scopes),
        }

    def authorize(
        self,
        command: OAuthAuthorizeCommand,
        active: ActiveBrowserSession,
    ) -> dict[str, str]:
        validated = self.validate_authorization_request(command)
        scopes = tuple(str(scope) for scope in validated["scopes"])
        require_scope_subset(scopes, set(active.credential.scopes))
        if command.action == "deny":
            return {
                "redirect_uri": redirect_with_params(
                    command.redirect_uri,
                    {"error": "access_denied", "state": command.state},
                )
            }
        raw_code = new_secret("code")
        now = datetime.now(UTC)
        self._session.add(
            OAuthAuthorizationGrantRecord(
                code_hash=secret_digest(raw_code),
                client_id=command.client_id,
                redirect_uri=command.redirect_uri,
                registered_redirect_uri=str(validated["registered_redirect_uri"]),
                actor_subject_id=active.user.user_id,
                scopes=list(scopes),
                code_challenge=command.code_challenge,
                challenge_method="S256",
                resource=command.resource,
                created_at=now,
                expires_at=now + timedelta(minutes=10),
                used_at=None,
                revoked_at=None,
            )
        )
        self._session.flush()
        return {
            "redirect_uri": redirect_with_params(
                command.redirect_uri,
                {"code": raw_code, "state": command.state},
            )
        }

    def exchange_code(
        self,
        command: OAuthAuthorizationCodeTokenCommand,
    ) -> dict[str, object]:
        require_oauth_resource(command.resource, self._public_base_url)
        now = datetime.now(UTC)
        grant = self._session.execute(
            select(OAuthAuthorizationGrantRecord)
            .where(OAuthAuthorizationGrantRecord.code_hash == secret_digest(command.code))
            .with_for_update()
        ).scalar_one_or_none()
        if (
            grant is None
            or grant.used_at is not None
            or grant.revoked_at is not None
            or grant.expires_at <= now
            or grant.client_id != command.client_id
            or grant.redirect_uri != command.redirect_uri
            or grant.resource != command.resource
            or grant.code_challenge != pkce_challenge(command.code_verifier)
        ):
            raise OAuthFlowError("invalid_grant", "authorization code is invalid")
        grant.used_at = now
        return self.issue_token_pair(
            actor_subject_id=grant.actor_subject_id,
            scopes=tuple(grant.scopes),
            auth_kind="pkce",
            client_id=grant.client_id,
            resource=command.resource,
            issued_at=now,
        )

    def exchange_refresh(
        self,
        command: OAuthRefreshTokenCommand,
    ) -> dict[str, object]:
        require_oauth_resource(command.resource, self._public_base_url)
        now = datetime.now(UTC)
        record = self._session.execute(
            select(CredentialRecord)
            .where(CredentialRecord.token_hash == secret_digest(command.refresh_token))
            .with_for_update()
        ).scalar_one_or_none()
        if (
            record is None
            or record.auth_kind != "refresh_token"
            or record.oauth_client_id != command.client_id
            or record.resource != command.resource
            or record.refresh_family_id is None
        ):
            raise OAuthFlowError("invalid_grant", "refresh token is invalid")
        if record.revoked_at is not None:
            self._revoke_family(record.refresh_family_id, now)
            raise OAuthFlowError(
                "invalid_grant",
                "refresh token reuse invalidated the token family",
            )
        if record.expires_at <= now:
            record.revoked_at = now
            raise OAuthFlowError("invalid_grant", "refresh token is expired")
        scopes = tuple(record.scopes)
        if command.scope is not None:
            scopes = parse_requested_scopes(command.scope)
            try:
                require_scope_subset(scopes, set(record.scopes))
            except AuthPolicyDenied as error:
                raise OAuthFlowError("invalid_scope", str(error)) from error
        record.revoked_at = now
        return self.issue_token_pair(
            actor_subject_id=record.actor_subject_id,
            scopes=scopes,
            auth_kind="oauth_refresh",
            client_id=command.client_id,
            resource=command.resource,
            issued_at=now,
            refresh_family_id=record.refresh_family_id,
        )

    def token_status(
        self,
        raw_token: str,
        *,
        allowed_auth_kinds: frozenset[str] | None = None,
        required_resource: str | None = None,
    ) -> dict[str, object]:
        repository = AuthRepository(self._session)
        try:
            credential = repository.mark_credential_used(secret_digest(raw_token))
            user = repository.get_user(credential.actor_subject_id)
        except (AuthRecordNotFound, CredentialUnavailable, ValueError) as error:
            raise AuthPolicyDenied("credential is missing, expired, or revoked") from error
        if user.status != "active":
            raise AuthPolicyDenied("credential subject is disabled")
        if (
            allowed_auth_kinds is not None
            and credential.auth_kind not in allowed_auth_kinds
        ):
            raise AuthPolicyDenied("credential kind is not accepted here")
        if required_resource is not None and credential.resource != required_resource:
            raise AuthPolicyDenied("credential audience does not match this resource")
        return {
            "credential_id": str(credential.credential_id),
            "actor_subject_id": credential.actor_subject_id,
            "actor_type": credential.actor_type,
            "auth_kind": credential.auth_kind,
            "book_id": None if credential.book_id is None else str(credential.book_id),
            "client_id": credential.oauth_client_id,
            "resource": credential.resource,
            "scopes": list(credential.scopes),
            "expires_at": credential.expires_at.isoformat(),
        }

    def revoke(
        self,
        raw_token: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, bool]:
        now = datetime.now(UTC)
        record = self._session.execute(
            select(CredentialRecord)
            .where(CredentialRecord.token_hash == secret_digest(raw_token))
            .with_for_update()
        ).scalar_one_or_none()
        if record is None or (
            client_id is not None and record.oauth_client_id != client_id
        ):
            return {"revoked": True}
        if record.auth_kind == "refresh_token" and record.refresh_family_id is not None:
            self._revoke_family(record.refresh_family_id, now)
        elif record.revoked_at is None:
            record.revoked_at = now
        self._session.flush()
        return {"revoked": True}

    def issue_token_pair(
        self,
        *,
        actor_subject_id: str,
        scopes: tuple[str, ...],
        auth_kind: str,
        client_id: str,
        resource: str,
        issued_at: datetime,
        refresh_family_id: UUID | None = None,
    ) -> dict[str, object]:
        if auth_kind not in OAUTH_ACCESS_KINDS:
            raise ValueError("auth_kind must identify an OAuth access token")
        require_oauth_resource(resource, self._public_base_url)
        user = self._session.get(UserRecord, actor_subject_id)
        client = self._session.get(OAuthClientRecord, client_id)
        if user is None or user.status != "active" or client is None or client.status != "active":
            raise OAuthFlowError("invalid_grant", "authorization subject is unavailable")
        family_id = refresh_family_id or uuid4()
        raw_access_token = new_secret("ta")
        raw_refresh_token = new_secret("rt")
        access_expires_at = issued_at + ACCESS_TOKEN_TTL
        refresh_expires_at = issued_at + REFRESH_TOKEN_TTL
        common = {
            "actor_subject_id": user.user_id,
            "actor_type": user.subject_type,
            "book_id": None,
            "oauth_client_id": client.client_id,
            "resource": resource,
            "refresh_family_id": family_id,
            "scopes": list(scopes),
            "issued_at": issued_at,
            "revoked_at": None,
            "last_used_at": None,
        }
        self._session.add_all(
            [
                CredentialRecord(
                    credential_id=uuid4(),
                    token_hash=secret_digest(raw_access_token),
                    jti=uuid4(),
                    auth_kind=auth_kind,
                    expires_at=access_expires_at,
                    **common,
                ),
                CredentialRecord(
                    credential_id=uuid4(),
                    token_hash=secret_digest(raw_refresh_token),
                    jti=uuid4(),
                    auth_kind="refresh_token",
                    expires_at=refresh_expires_at,
                    **common,
                ),
            ]
        )
        self._session.flush()
        return {
            "access_token": raw_access_token,
            "token_type": "Bearer",
            "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
            "refresh_token": raw_refresh_token,
            "refresh_token_expires_in": int(REFRESH_TOKEN_TTL.total_seconds()),
            "scope": " ".join(scopes),
            "resource": resource,
        }

    def _active_client(self, client_id: str) -> OAuthClientRecord:
        client = self._session.get(OAuthClientRecord, client_id)
        if client is None or client.status != "active":
            raise AuthSecurityError("unknown OAuth client")
        return client

    def _require_redirect(self, client_id: str, redirect_uri: str) -> str:
        requested = validate_redirect_uri(redirect_uri)
        registered = self._session.scalars(
            select(OAuthClientRedirectUriRecord).where(
                OAuthClientRedirectUriRecord.client_id == client_id,
                OAuthClientRedirectUriRecord.status == "active",
            )
        )
        for row in registered:
            if redirect_uri_matches(row.redirect_uri, requested):
                return row.redirect_uri
        raise AuthSecurityError("redirect_uri is not registered")

    def _revoke_family(self, family_id: UUID, revoked_at: datetime) -> None:
        records = self._session.scalars(
            select(CredentialRecord)
            .where(CredentialRecord.refresh_family_id == family_id)
            .with_for_update()
        )
        for record in records:
            if record.revoked_at is None:
                record.revoked_at = revoked_at
        self._session.flush()


__all__ = [
    "ACCESS_TOKEN_TTL",
    "OAUTH_ACCESS_KINDS",
    "PersistentOAuthService",
    "REFRESH_TOKEN_TTL",
]
