from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_auth_pages_callback_does_not_use_platform_exchange_singleton() -> None:
    source = (BACKEND / "api_routers/auth_pages.py").read_text()
    ports = (BACKEND / "api_ports/auth_pages.py").read_text()
    service_source = (BACKEND / "service_platform_auth.py").read_text()

    assert "platform_key_exchange" not in source
    assert "service.authorize_platform_oauth(" not in source
    assert "from ..api_ports.auth_pages import AuthPagesService" in source
    assert "class AuthPagesRouteService(Protocol)" in ports
    assert "AuthPagesService = Annotated[AuthPagesRouteService, ServiceDependency]" in ports
    assert "def authorize_platform_oauth_for_actor(" in service_source
