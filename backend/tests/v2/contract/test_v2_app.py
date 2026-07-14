from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
API_MODULE = REPOSITORY_ROOT / "backend/app/track_anywhere/api.py"
API_PACKAGE = REPOSITORY_ROOT / "backend/app/track_anywhere/api"
DUMMY_RUNTIME_URL = (
    "postgresql+psycopg://track_anywhere_runtime:secret@127.0.0.1:9/track_anywhere"
)


@pytest.fixture(autouse=True)
def _runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACK_ANYWHERE_DATABASE_URL", DUMMY_RUNTIME_URL)
    monkeypatch.setenv("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", "0")


def test_api_entrypoint_is_a_package_and_import_has_no_database_io_or_v1_runtime(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "TRACK_ANYWHERE_AUTH_COOKIE_SECURE": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    (
                        str(REPOSITORY_ROOT / "backend/app"),
                        environment.get("PYTHONPATH"),
                    ),
                )
            ),
        }
    )
    environment.pop("TRACK_ANYWHERE_DATABASE_URL", None)
    environment.pop("TRACK_ANYWHERE_FAST_TEST_SCHEMA", None)
    script = textwrap.dedent(
        """
        import json
        import sys

        from sqlalchemy.engine import Engine
        from fastapi.testclient import TestClient

        def reject_database_io(*args, **kwargs):
            raise AssertionError("database I/O occurred while importing the API")

        Engine.connect = reject_database_io
        from alembic import command
        command.upgrade = reject_database_io

        import track_anywhere.api as api

        paths = {route.path for route in api.app.routes}
        client = TestClient(api.app)
        health = client.get("/api/v2/health")
        ready = client.get("/api/v2/ready")

        forbidden_prefixes = (
            "track_anywhere.api_auth_runtime",
            "track_anywhere.api_routes",
            "track_anywhere.api_runtime",
            "track_anywhere.api_routers",
            "track_anywhere.db_migrations",
            "track_anywhere.service",
            "track_anywhere.storage",
        )
        forbidden = sorted(
            name
            for name in sys.modules
            if name == forbidden_prefixes or name.startswith(forbidden_prefixes)
        )
        print(
            json.dumps(
                {
                    "is_package": hasattr(api, "__path__"),
                    "has_app": hasattr(api, "app"),
                    "has_service": hasattr(api, "service"),
                    "forbidden": forbidden,
                    "has_health": "/api/v2/health" in paths,
                    "has_ready": "/api/v2/ready" in paths,
                    "only_v2": all(path.startswith("/api/v2") for path in paths),
                    "health": [health.status_code, health.json()],
                    "ready": [ready.status_code, ready.json()],
                }
            )
        )
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "is_package": True,
        "has_app": True,
        "has_service": False,
        "forbidden": [],
        "has_health": True,
        "has_ready": True,
        "only_v2": True,
        "health": [200, {"status": "ok", "api_version": "v2"}],
        "ready": [
            503,
            {
                "status": "error",
                "api_version": "v2",
                "checks": {"database": "error", "schema": "error"},
            },
        ],
    }
    assert API_PACKAGE.is_dir()
    assert not API_MODULE.exists()


def test_create_app_mounts_only_v2_routes_and_injects_router_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependencies_module = importlib.import_module("track_anywhere.api.dependencies")
    app_module = importlib.import_module("track_anywhere.api.app")
    dependencies = dependencies_module.build_runtime_dependencies(DUMMY_RUNTIME_URL)
    calls: dict[str, Any] = {}

    def create_system_router(*, engine: object, expected_runtime_role: str) -> APIRouter:
        calls["system"] = (engine, expected_runtime_role)
        router = APIRouter()
        router.add_api_route("/health", lambda: {"status": "ok"}, methods=["GET"])
        return router

    def create_auth_router(get_session: object, *, cookie_secure: bool) -> APIRouter:
        calls["auth"] = (get_session, cookie_secure)
        router = APIRouter(prefix="/api/v2")
        router.add_api_route("/session", lambda: {"authenticated": False}, methods=["GET"])
        return router

    application = app_module.create_app(
        dependencies=dependencies,
        system_router_factory=create_system_router,
        auth_router_factory=create_auth_router,
        cookie_secure=True,
    )

    paths = {route.path for route in application.routes}
    assert paths >= {
        "/api/v2/docs",
        "/api/v2/health",
        "/api/v2/openapi.json",
        "/api/v2/session",
    }
    assert all(path.startswith("/api/v2") for path in paths)
    assert calls == {
        "system": (dependencies.engine, "track_anywhere_runtime"),
        "auth": (dependencies.get_session, True),
    }


def test_create_app_composes_the_runtime_url_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.engine import Engine

    app_module = importlib.import_module("track_anywhere.api.app")

    def reject_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("app composition must not connect to PostgreSQL")

    monkeypatch.setattr(Engine, "connect", reject_connect)
    application = app_module.create_app()
    runtime = application.state.runtime_dependencies

    assert runtime is not None
    assert runtime.engine.url.render_as_string(hide_password=False) == DUMMY_RUNTIME_URL
    assert runtime.expected_runtime_role == "track_anywhere_runtime"


class _SessionProbe:
    def __init__(self) -> None:
        self.operations: list[str] = []

    def commit(self) -> None:
        self.operations.append("commit")

    def rollback(self) -> None:
        self.operations.append("rollback")

    def close(self) -> None:
        self.operations.append("close")


def test_request_session_dependency_commits_and_closes() -> None:
    dependencies_module = importlib.import_module("track_anywhere.api.dependencies")
    session = _SessionProbe()
    get_session = dependencies_module.create_session_dependency(lambda: session)

    dependency = get_session()
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)

    assert session.operations == ["commit", "close"]


def test_request_session_dependency_rolls_back_and_closes() -> None:
    dependencies_module = importlib.import_module("track_anywhere.api.dependencies")
    session = _SessionProbe()
    get_session = dependencies_module.create_session_dependency(lambda: session)

    dependency = get_session()
    assert next(dependency) is session
    with pytest.raises(RuntimeError, match="command failed"):
        dependency.throw(RuntimeError("command failed"))

    assert session.operations == ["rollback", "close"]


def test_api_package_has_no_v1_composition_imports() -> None:
    forbidden = (
        "FinanceService",
        "OrmStorage",
        "api_auth_runtime",
        "api_routes",
        "api_runtime",
        "api_routers",
        "db_migrations",
        "hydration",
        "storage_read_cache",
    )

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(API_PACKAGE.rglob("*.py"))
    )

    assert source
    assert [token for token in forbidden if token in source] == []
