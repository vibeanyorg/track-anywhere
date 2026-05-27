from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_oauth_router_uses_service_dependency_boundary():
    source = (BACKEND / "api_routers/oauth.py").read_text()
    ports = (BACKEND / "api_ports/oauth.py").read_text()

    assert "from ..api_runtime import" not in source
    assert "platform_key_exchange" not in source
    assert "from ..api_ports.oauth import OAuthService" in source
    assert "class OAuthRouteService(Protocol)" in ports
    assert "OAuthService = Annotated[OAuthRouteService, ServiceDependency]" in ports
