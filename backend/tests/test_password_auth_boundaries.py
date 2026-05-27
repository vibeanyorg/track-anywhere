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
    assert "create_password_account_store" in source
