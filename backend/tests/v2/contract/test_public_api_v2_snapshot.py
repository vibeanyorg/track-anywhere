from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine


SNAPSHOT = Path(__file__).parents[2] / "snapshots" / "public-api-v2.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def test_public_v2_openapi_matches_reviewed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OpenAPI composition is connection-free. A PostgreSQL-shaped engine keeps
    # all database-backed routers mounted without coupling this snapshot test
    # to migration execution.
    engine = create_engine(
        "postgresql+psycopg://track_anywhere_runtime:test@127.0.0.1:9/contract"
    )
    try:
        monkeypatch.delenv("TRACK_ANYWHERE_DATABASE_URL", raising=False)
        from track_anywhere.api import create_app

        application = create_app(
            engine=engine,
            expected_runtime_role="track_anywhere_runtime",
            cookie_secure=False,
        )
        openapi = application.openapi()
        paths = {
            path: sorted(method for method in operations if method in HTTP_METHODS)
            for path, operations in sorted(openapi["paths"].items())
        }
        schemas = {
            "PasswordSessionCommand": openapi["components"]["schemas"][
                "PasswordSessionCommand"
            ],
            "PasswordSignupCommand": openapi["components"]["schemas"][
                "PasswordSignupCommand"
            ],
            "ReportingLineResponse": openapi["components"]["schemas"][
                "ReportingLineResponse"
            ]
        }
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

        assert {"paths": paths, "schemas": schemas} == expected
        assert paths
        assert all(path.startswith("/api/v2/") for path in paths)
        assert not any("/dev-" in path for path in paths)
        assert paths["/api/v2/auth/signup"] == ["post"]
        assert paths["/api/v2/auth/session/password"] == ["post"]
        password_session = schemas["PasswordSessionCommand"]
        assert set(password_session["required"]) == {"email", "password"}
        assert password_session["properties"]["password"]["minLength"] == 12
        assert password_session["properties"]["password"]["maxLength"] == 128
        password_signup = schemas["PasswordSignupCommand"]
        assert set(password_signup["required"]) == {
            "display_name",
            "email",
            "password",
            "setup_key",
        }
        assert password_signup["properties"]["password"]["minLength"] == 12
        assert password_signup["properties"]["password"]["maxLength"] == 128
    finally:
        engine.dispose()
