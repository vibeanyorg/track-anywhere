from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend/app/track_anywhere"


def test_password_auth_is_persistence_agnostic():
    source = (BACKEND / "password_auth.py").read_text()

    assert "sqlalchemy" not in source
    assert "PasswordAccountRecord" not in source
    assert "session_factory" not in source
    assert "_accounts" not in source
    assert "class PasswordAccountRepository(Protocol)" in source


def test_api_runtime_does_not_reach_into_storage_sessions():
    source = (BACKEND / "api_runtime.py").read_text()

    assert "service.storage" not in source
    assert "session_factory" not in source
    assert "create_password_account_store" not in source


def test_password_store_is_not_exposed_through_api_runtime_or_routers():
    runtime_source = (BACKEND / "api_runtime.py").read_text()
    api_source = (BACKEND / "api_routers/auth.py").read_text()
    page_source = (BACKEND / "api_routers/auth_pages.py").read_text()
    service_source = (BACKEND / "service_password_auth.py").read_text()

    assert "password_accounts" not in runtime_source
    assert "password_accounts" not in api_source
    assert "password_accounts" not in page_source
    assert "def create_password_account_store(" not in service_source
    assert "OAuthIdentity(provider=\"password\"" not in api_source
    assert "OAuthIdentity(provider=\"password\"" not in page_source
    assert "def authenticate_password_account(" in service_source
    assert "def create_password_account(" in service_source
    assert "def login_password_account(" in service_source
