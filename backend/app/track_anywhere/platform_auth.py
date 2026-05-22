from __future__ import annotations

from .errors import PolicyDenied, ValidationError
from .oauth_grants import AuthorizationGrant, DeviceGrant
from .platform_auth_models import (
    ACCESS_TOKEN_TTL,
    AUTHORIZATION_CODE_TTL,
    DEFAULT_PLATFORM_CLIENT_ID,
    DEVICE_CODE_TTL,
    DEVICE_GRANT_TYPE,
    ApiKeySessionCommand,
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
from .security import Actor, hash_secret, utcnow
from .service_auth import AGENT_ALLOWED_SCOPES


class PlatformKeyExchange:
    def __init__(self) -> None:
        self._clients: dict[str, PlatformClient] = {}
        self._codes: dict[str, AuthorizationGrant] = {}
        self._devices: dict[str, DeviceGrant] = {}
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
        return self.client_public_dict(client)

    def list_clients(self) -> list[dict[str, object]]:
        clients = sorted(self._clients.values(), key=lambda client: (client.client_name, client.client_id))
        return [self.client_public_dict(client) for client in clients]

    def authorize(self, command: OAuthAuthorizeCommand, actor: Actor, storage=None) -> dict[str, str]:
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
        self._codes[grant.code_hash] = grant
        if storage is not None:
            storage.save_authorization_grant(grant)
        return {"redirect_uri": redirect_with_params(redirect_uri, {"code": code, "state": command.state})}

    def exchange_code(self, command: OAuthTokenCommand, service) -> dict[str, object]:
        code_hash = hash_secret(command.code)
        grant = service.storage.load_authorization_grant(code_hash) or self._codes.get(code_hash)
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
        service.storage.save_authorization_grant(grant)
        return self._issue_platform_token(service, grant.actor, grant.scopes, grant.client_id, grant.resource, "pkce")

    def create_device_authorization(self, command: OAuthDeviceAuthorizeCommand, issuer: str, storage=None) -> dict[str, object]:
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
        self._devices[grant.device_code_hash] = grant
        if storage is not None:
            storage.save_device_grant(grant)
        verification_uri = f"{base}/api/v1/auth/device"
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": f"{verification_uri}?user_code={user_code}",
            "expires_in": int(DEVICE_CODE_TTL.total_seconds()),
            "interval": grant.interval_seconds,
        }

    def approve_device_user_code(self, user_code: str, actor: Actor, action: str, storage=None, approved_scopes: list[str] | None = None) -> DeviceGrant:
        user_hash = hash_secret(normalize_user_code(user_code))
        grant = (storage.load_device_grant_by_user_hash(user_hash) if storage is not None else None) or self._device_by_user_hash(user_hash)
        if grant is None or grant.expires_at <= utcnow() or grant.status != "pending":
            raise PolicyDenied("device code is invalid or expired")
        if action == "deny":
            grant.status = "denied"
        else:
            scopes = tuple(dict.fromkeys(approved_scopes)) if approved_scopes is not None else grant.scopes
            if not scopes:
                raise ValidationError("select at least one scope")
            unexpected_scopes = set(scopes) - set(grant.scopes)
            if unexpected_scopes:
                raise ValidationError(f"approved scopes were not requested: {sorted(unexpected_scopes)}")
            self._ensure_actor_can_approve(scopes, actor)
            grant.scopes = scopes
            grant.status = "approved"
            grant.approved_actor = actor
            grant.approved_at = utcnow()
        self._devices[grant.device_code_hash] = grant
        if storage is not None:
            storage.save_device_grant(grant)
        return grant

    def exchange_device_code(self, command: OAuthDeviceTokenCommand, service) -> dict[str, object]:
        device_hash = hash_secret(command.device_code)
        grant = service.storage.load_device_grant_by_device_hash(device_hash) or self._devices.get(device_hash)
        if grant is None:
            raise OAuthTokenError("invalid_grant", "device code is invalid")
        if grant.client_id != command.client_id or grant.resource != command.resource:
            raise OAuthTokenError("invalid_grant", "device code client or resource mismatch")
        try:
            self._record_poll(grant)
        except OAuthTokenError:
            service.storage.save_device_grant(grant)
            raise
        if grant.expires_at <= utcnow():
            grant.status = "expired"
            service.storage.save_device_grant(grant)
            raise OAuthTokenError("expired_token", "device code expired")
        if grant.status == "pending":
            service.storage.save_device_grant(grant)
            raise OAuthTokenError("authorization_pending", "authorization is still pending")
        if grant.status == "denied":
            service.storage.save_device_grant(grant)
            raise OAuthTokenError("access_denied", "device authorization was denied")
        if grant.status != "approved" or grant.approved_actor is None:
            service.storage.save_device_grant(grant)
            raise OAuthTokenError("invalid_grant", "device code is no longer valid")
        actor = grant.approved_actor
        grant.status = "consumed"
        service.storage.save_device_grant(grant)
        return self._issue_platform_token(service, actor, grant.scopes, grant.client_id, grant.resource, "device")

    def revoke(self, command: OAuthRevokeCommand, service) -> dict[str, bool]:
        service.credentials.revoke(command.token)
        credential = service.credentials.get_by_token(command.token)
        if credential is not None:
            service.storage.save_credential(credential)
        return {"revoked": True}

    def authorization_server_metadata(self, issuer: str) -> dict[str, object]:
        base = issuer.rstrip("/")
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/api/v1/oauth/authorize",
            "token_endpoint": f"{base}/api/v1/oauth/token",
            "device_authorization_endpoint": f"{base}/api/v1/oauth/device/authorize",
            "registration_endpoint": f"{base}/api/v1/oauth/register",
            "revocation_endpoint": f"{base}/api/v1/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", DEVICE_GRANT_TYPE],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": sorted(AGENT_ALLOWED_SCOPES),
        }

    def protected_resource_metadata(self, issuer: str) -> dict[str, object]:
        base = issuer.rstrip("/")
        return {"resource": f"{base}/api/v1", "authorization_servers": [base], "bearer_methods_supported": ["header"], "scopes_supported": sorted(AGENT_ALLOWED_SCOPES)}

    def client_public_dict(self, client: PlatformClient) -> dict[str, object]:
        return {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris),
            "grant_types": ["authorization_code", DEVICE_GRANT_TYPE],
            "response_types": ["code"],
            "scope": " ".join(client.scopes),
            "token_endpoint_auth_method": "none",
            "client_uri": client.client_uri,
            "logo_uri": client.logo_uri,
        }

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

    def _issue_platform_token(self, service, actor: Actor, scopes, client_id: str, resource: str | None, auth_kind: str) -> dict[str, object]:
        access_token = service.credentials.issue(
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            scopes=set(scopes),
            ttl=ACCESS_TOKEN_TTL,
            auth_kind=auth_kind,
            name=f"{auth_kind} token for {client_id}",
            created_by_actor_id=actor.actor_id,
        )
        credential = service.credentials.get_by_token(access_token)
        if credential is None:
            raise RuntimeError("issued credential was not stored")
        audit_event = service.audit.record(
            operation=f"oauth.{auth_kind}.exchange",
            actor=actor,
            entity_ref=client_id,
            details={"scopes": sorted(scopes), "resource": resource},
        )
        service.storage.save_credential_and_audit_event(credential, audit_event)
        return {"access_token": access_token, "token_type": "Bearer", "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()), "scope": " ".join(scopes)}

    def _device_by_user_hash(self, user_hash: str) -> DeviceGrant | None:
        return next((grant for grant in self._devices.values() if grant.user_code_hash == user_hash), None)

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
