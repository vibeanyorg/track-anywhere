from __future__ import annotations

from .platform_auth_models import DEVICE_GRANT_TYPE, PlatformClient
from .service_auth import AGENT_ALLOWED_SCOPES


def authorization_server_metadata(issuer: str) -> dict[str, object]:
    base = issuer.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/v2/oauth/authorize",
        "token_endpoint": f"{base}/api/v2/oauth/token",
        "device_authorization_endpoint": f"{base}/api/v2/oauth/device/authorize",
        "registration_endpoint": f"{base}/api/v2/oauth/register",
        "revocation_endpoint": f"{base}/api/v2/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", DEVICE_GRANT_TYPE],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(AGENT_ALLOWED_SCOPES),
    }


def protected_resource_metadata(issuer: str) -> dict[str, object]:
    base = issuer.rstrip("/")
    return {
        "resource": f"{base}/api/v2",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": sorted(AGENT_ALLOWED_SCOPES),
    }


def client_public_dict(client: PlatformClient) -> dict[str, object]:
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
