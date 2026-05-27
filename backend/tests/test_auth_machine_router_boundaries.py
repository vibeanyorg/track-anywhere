from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_auth_machine_pages_use_narrow_service_port_and_ui_helpers() -> None:
    source = (BACKEND / "api_routers/auth_machine_pages.py").read_text()
    ui_source = (BACKEND / "api_routers/auth_page_ui.py").read_text()
    ports = (BACKEND / "api_ports/auth_machine.py").read_text()

    assert "from ..api_runtime import browser_sessions, service" not in source
    assert "from ..api_runtime import browser_sessions" not in source
    assert "from ..api_runtime import service" not in source
    assert "from ..api_browser_sessions import browser_sessions" in source
    assert "from .auth_pages import _error, _hidden, _page" not in source
    assert "from ..api_ports.auth_machine import AuthMachineService" in source
    assert "from .auth_page_ui import error_message, hidden_input, render_auth_page" in source
    assert "..api_runtime" not in ui_source
    assert "APIRouter" not in ui_source
    assert "def render_auth_page(" in ui_source
    assert "def hidden_input(" in ui_source
    assert "def error_message(" in ui_source
    assert "class AuthMachineRouteService(Protocol)" in ports
    assert "AuthMachineService = Annotated[AuthMachineRouteService, ServiceDependency]" in ports
