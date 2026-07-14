from __future__ import annotations

from .errors import PolicyDenied, ValidationError
from .oauth_grants import AuthorizationGrant, DeviceGrant
from .platform_auth_models import (
    ACCESS_TOKEN_TTL,
    AUTHORIZATION_CODE_TTL,
    DEFAULT_PLATFORM_CLIENT_ID,
    DEVICE_CODE_TTL,
    OAuthAuthorizeCommand,
    OAuthDeviceAuthorizeCommand,
    OAuthDeviceTokenCommand,
    OAuthRegisterCommand,
    OAuthRevokeCommand,
    OAuthTokenCommand,
    OAuthTokenError,
    PlatformClient,
    new_user_code,
    normalize_user_code,
    parse_requested_scopes,
    pkce_challenge,
    redirect_uri_allowed,
    redirect_with_params,
    validate_redirect_uri,
)
from .platform_auth_metadata import client_public_dict
from .security import Actor, hash_secret, utcnow
from .service_auth import AGENT_ALLOWED_SCOPES


class PlatformKeyExchange:
    def __init__(self) -> None:
        self._clients: dict[str, PlatformClient] = {}
        self._register_default_client()

    def register_client(self, command: OAuthRegisterCommand) -> dict[str, object]:
        redirect_uris = tuple(validate_redirect_uri(uri) for uri in command.redirect_uris)
        scopes = parse_requested_scopes(command.scope)
        unknown_scopes = set(scopes) - AGENT_ALLOWED_SCOPES
        if unknown_scopes:
            raise ValidationError(f"unknown OAuth scopes: {sorted(unknown_scopes)}")
        import secrets
        client = PlatformClient(
            client_id=f"client_{secrets.token_urlsafe(24)}",
            client_name=command.client_name,
            redirect_uris=redirect_uris,
            scopes=tuple(scopes),
            client_uri=command.client_uri,
            logo_uri=command.logo_uri,
        )
        self._clients[client.client_id] = client
        return client_public_dict(client)

    def list_clients(self) -> list[dict[str, object]]:
        clients = sorted(self._clients.values(), key=lambda client: (client.client_name, client.client_id))
        return [client_public_dict(client) for client in clients]

    def authorize(self, command: OAuthAuthorizeCommand, actor: Actor, *, grant_store) -> dict[str, str]:
        if command.action == "deny":
            return {"redirect_uri": redirect_with_params(command.redirect_uri, {"error": "access_denied", "state": command.state})}
        client = self._client_for(command.client_id)
        redirect_uri = validate_redirect_uri(command.redirect_uri)
        if not redirect_uri_allowed(client, redirect_uri):
            raise ValidationError("redirect_uri is not registered for this client")
        scopes = self._validated_scopes(command.scope, client, actor)
        code = self._new_secret("code")
        grant = AuthorizationGrant(
            code_hash=hash_secret(code),
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            actor=actor,
            scopes=tuple(scopes),
            code_challenge=command.code_challenge,
            resource=command.resource,
            expires_at=utcnow() + AUTHORIZATION_CODE_TTL,
        )
        grant_store.save_authorization_grant(grant)
        return {"redirect_uri": redirect_with_params(redirect_uri, {"code": code, "state": command.state})}

    def exchange_code(self, command: OAuthTokenCommand, *, grant_store, credentials, audit, credential_writer) -> dict[str, object]:
        code_hash = hash_secret(command.code)
        grant = grant_store.load_authorization_grant(code_hash)
        if grant is None or grant.used or grant.expires_at <= utcnow():
            raise PolicyDenied("authorization code is invalid or expired")
        if grant.client_id != command.client_id:
            raise PolicyDenied("authorization code client mismatch")
        if grant.redirect_uri != validate_redirect_uri(command.redirect_uri):
            raise PolicyDenied("authorization code redirect_uri mismatch")
        if grant.resource != command.resource:
            raise PolicyDenied("authorization code resource mismatch")
        if pkce_challenge(command.code_verifier) != grant.code_challenge:
            raise PolicyDenied("invalid PKCE code verifier")
        grant.used = True
        grant_store.save_authorization_grant(grant)
        return self._issue_platform_token(
            credentials=credentials,
            audit=audit,
            credential_writer=credential_writer,
            actor=grant.actor,
            scopes=grant.scopes,
            client_id=grant.client_id,
            resource=grant.resource,
            auth_kind="pkce",
        )

    def create_device_authorization(self, command: OAuthDeviceAuthorizeCommand, issuer: str, *, grant_store) -> dict[str, object]:
        client = self._client_for(command.client_id)
        scopes = self._validated_client_scopes(command.scope, client)
        device_code = self._new_secret("dev")
        user_code = new_user_code()
        base = issuer.rstrip("/")
        grant = DeviceGrant(
            device_code_hash=hash_secret(device_code),
            user_code_hash=hash_secret(normalize_user_code(user_code)),
            client_id=client.client_id,
            scopes=tuple(scopes),
            resource=command.resource,
            status="pending",
            expires_at=utcnow() + DEVICE_CODE_TTL,
            interval_seconds=5,
            created_at=utcnow(),
        )
        grant_store.save_device_grant(grant)
        verification_uri = f"{base}/api/v2/auth/device"
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": f"{verification_uri}?user_code={user_code}",
            "expires_in": int(DEVICE_CODE_TTL.total_seconds()),
            "interval": grant.interval_seconds,
        }

    def approve_device_user_code(
        self,
        user_code: str,
        actor: Actor,
        action: str,
        *,
        grant_store,
        approved_scopes: list[str] | None = None,
    ) -> DeviceGrant:
        user_hash = hash_secret(normalize_user_code(user_code))
        grant = grant_store.load_device_grant_by_user_hash(user_hash)
        if grant is None or grant.expires_at <= utcnow() or grant.status != "pending":
            raise PolicyDenied("device code is invalid or expired")
        if action == "deny":
            grant.status = "denied"
        else:
            scopes = (
                tuple(self._validated_client_scopes(" ".join(approved_scopes), self._client_for(grant.client_id)))
                if approved_scopes is not None
                else grant.scopes
            )
            self._ensure_actor_can_approve(scopes, actor)
            grant.scopes = scopes
            grant.status = "approved"
            grant.approved_actor = actor
            grant.approved_at = utcnow()
        grant_store.save_device_grant(grant)
        return grant

    def exchange_device_code(self, command: OAuthDeviceTokenCommand, *, grant_store, credentials, audit, credential_writer) -> dict[str, object]:
        device_hash = hash_secret(command.device_code)
        grant = grant_store.load_device_grant_by_device_hash(device_hash)
        if grant is None:
            raise OAuthTokenError("invalid_grant", "device code is invalid")
        if grant.client_id != command.client_id or grant.resource != command.resource:
            raise OAuthTokenError("invalid_grant", "device code client or resource mismatch")
        try:
            self._record_poll(grant)
        except OAuthTokenError:
            grant_store.save_device_grant(grant)
            raise
        if grant.expires_at <= utcnow():
            grant.status = "expired"
            grant_store.save_device_grant(grant)
            raise OAuthTokenError("expired_token", "device code expired")
        if grant.status == "pending":
            grant_store.save_device_grant(grant)
            raise OAuthTokenError("authorization_pending", "authorization is still pending")
        if grant.status == "denied":
            grant_store.save_device_grant(grant)
            raise OAuthTokenError("access_denied", "device authorization was denied")
        if grant.status != "approved" or grant.approved_actor is None:
            grant_store.save_device_grant(grant)
            raise OAuthTokenError("invalid_grant", "device code is no longer valid")
        actor = grant.approved_actor
        grant.status = "consumed"
        grant_store.save_device_grant(grant)
        return self._issue_platform_token(
            credentials=credentials,
            audit=audit,
            credential_writer=credential_writer,
            actor=actor,
            scopes=grant.scopes,
            client_id=grant.client_id,
            resource=grant.resource,
            auth_kind="device",
        )

    def revoke(self, command: OAuthRevokeCommand, *, credentials, credential_writer) -> dict[str, bool]:
        credentials.revoke(command.token)
        credential = credentials.get_by_token(command.token)
        if credential is not None:
            credential_writer.save_credential(credential)
        return {"revoked": True}

    def _client_for(self, client_id: str) -> PlatformClient:
        client = self._clients.get(client_id)
        if client is None:
            raise ValidationError("unknown OAuth client")
        return client

    def _validated_client_scopes(self, scope: str, client: PlatformClient) -> list[str]:
        scopes = parse_requested_scopes(scope)
        unknown_scopes = set(scopes) - set(client.scopes)
        if unknown_scopes:
            raise ValidationError(f"client is not allowed to request scopes: {sorted(unknown_scopes)}")
        if "credential:write" in scopes:
            raise PolicyDenied("platform exchanges may not mint credential:write tokens")
        return scopes

    def _validated_scopes(self, scope: str, client: PlatformClient, actor: Actor) -> list[str]:
        scopes = self._validated_client_scopes(scope, client)
        self._ensure_actor_can_approve(scopes, actor)
        return scopes

    @staticmethod
    def _ensure_actor_can_approve(scopes, actor: Actor) -> None:
        disallowed_scopes = set(scopes) - set(actor.scopes)
        if disallowed_scopes:
            raise PolicyDenied(f"actor lacks requested scopes: {sorted(disallowed_scopes)}")

    def _issue_platform_token(
        self,
        *,
        credentials,
        audit,
        credential_writer,
        actor: Actor,
        scopes,
        client_id: str,
        resource: str | None,
        auth_kind: str,
    ) -> dict[str, object]:
        access_token = credentials.issue(
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            scopes=set(scopes),
            ttl=ACCESS_TOKEN_TTL,
            auth_kind=auth_kind,
            name=f"{auth_kind} token for {client_id}",
            created_by_actor_id=actor.actor_id,
        )
        credential = credentials.get_by_token(access_token)
        if credential is None:
            raise RuntimeError("issued credential was not stored")
        audit_event = audit.record(
            operation=f"oauth.{auth_kind}.exchange",
            actor=actor,
            entity_ref=client_id,
            details={"scopes": sorted(scopes), "resource": resource},
        )
        credential_writer.save_credential_and_audit_event(credential, audit_event)
        return {"access_token": access_token, "token_type": "Bearer", "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()), "scope": " ".join(scopes)}

    def _record_poll(self, grant: DeviceGrant) -> None:
        now = utcnow()
        if grant.last_poll_at is not None and (now - grant.last_poll_at).total_seconds() < grant.interval_seconds:
            grant.interval_seconds += 5
            grant.last_poll_at = now
            grant.poll_count += 1
            raise OAuthTokenError("slow_down", "polling too quickly", {"interval": grant.interval_seconds})
        grant.last_poll_at = now
        grant.poll_count += 1

    def _register_default_client(self) -> None:
        scopes = tuple(sorted(AGENT_ALLOWED_SCOPES))
        self._clients[DEFAULT_PLATFORM_CLIENT_ID] = PlatformClient(
            client_id=DEFAULT_PLATFORM_CLIENT_ID,
            client_name="Track Anywhere Web",
            redirect_uris=("http://localhost:3000/auth/callback", "http://127.0.0.1:3000/auth/callback"),
            scopes=scopes,
        )

    @staticmethod
    def _new_secret(prefix: str) -> str:
        import secrets
        return f"{prefix}_{secrets.token_urlsafe(32)}"
