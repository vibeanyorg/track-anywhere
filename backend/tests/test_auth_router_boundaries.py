from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_auth_router_uses_service_dependency_boundary() -> None:
    source = (BACKEND / "api_routers/auth.py").read_text()
    ports = (BACKEND / "api_ports/auth.py").read_text()
    runtime = (BACKEND / "api_runtime.py").read_text()

    assert "from ..api_runtime import" not in source
    assert "browser_sessions" not in runtime
    assert not re.search(r"\bservice\.", source)
    assert "from ..api_browser_sessions import browser_sessions" in source
    assert "from ..api_ports.auth import AuthService" in source
    assert "class AuthRouteService(AuditRecorder, Protocol)" in ports
    assert "AuthService = Annotated[AuthRouteService, ServiceDependency]" in ports


def test_auth_http_runtime_is_not_part_of_service_runtime() -> None:
    runtime = (BACKEND / "api_runtime.py").read_text()
    app_source = (BACKEND / "api.py").read_text()
    auth_router = (BACKEND / "api_routers/auth.py").read_text()
    auth_pages_router = (BACKEND / "api_routers/auth_pages.py").read_text()
    system_router = (BACKEND / "api_routers/system.py").read_text()

    assert "auth_settings_from_env" not in runtime
    assert "build_oauth_registry" not in runtime
    assert "ALLOWED_ORIGINS" not in runtime
    assert "auth_cookie_secure" not in runtime
    assert "from .api_auth_runtime import ALLOWED_ORIGINS, auth_cookie_secure, auth_settings" in app_source
    assert "from ..api_auth_runtime import auth_cookie_secure, auth_settings, oauth_registry" in auth_router
    assert "from ..api_auth_runtime import auth_cookie_secure, auth_settings" in auth_pages_router
    assert "from ..api_auth_runtime import auth_cookie_secure" in system_router
