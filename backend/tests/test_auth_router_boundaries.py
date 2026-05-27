from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_auth_router_uses_service_dependency_boundary() -> None:
    source = (BACKEND / "api_routers/auth.py").read_text()
    ports = (BACKEND / "api_ports/auth.py").read_text()
    runtime = (BACKEND / "api_runtime.py").read_text()

    assert "from ..api_runtime import service" not in source
    assert "browser_sessions" not in runtime
    assert not re.search(r"\bservice\.", source)
    assert "from ..api_browser_sessions import browser_sessions" in source
    assert "from ..api_ports.auth import AuthService" in source
    assert "class AuthRouteService(AuditRecorder, Protocol)" in ports
    assert "AuthService = Annotated[AuthRouteService, ServiceDependency]" in ports
