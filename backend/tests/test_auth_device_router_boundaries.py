from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_auth_device_pages_use_service_port_without_exchange_singleton() -> None:
    source = (BACKEND / "api_routers/auth_device_pages.py").read_text()
    ports = (BACKEND / "api_ports/auth_device.py").read_text()
    service_source = (BACKEND / "service_platform_auth.py").read_text()

    assert "from ..api_runtime import browser_sessions, platform_key_exchange, service" not in source
    assert "from ..api_runtime import browser_sessions" not in source
    assert "platform_key_exchange" not in source
    assert "from ..api_runtime import service" not in source
    assert "from ..api_browser_sessions import browser_sessions" in source
    assert "from ..api_ports.auth_device import AuthDeviceService" in source
    assert "from .auth_page_ui import error_message, hidden_input, render_auth_page" in source
    assert "class AuthDeviceRouteService(Protocol)" in ports
    assert "AuthDeviceService = Annotated[AuthDeviceRouteService, ServiceDependency]" in ports
    assert "def approve_platform_device_user_code_for_actor(" in service_source
