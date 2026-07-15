from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from track_anywhere.server import create_server


def _write_static_export(root: Path) -> None:
    (root / "auth" / "login").mkdir(parents=True)
    (root / "auth" / "callback").mkdir(parents=True)
    (root / "_next" / "static").mkdir(parents=True)
    (root / "index.html").write_text("<h1>Track Anywhere</h1>", encoding="utf-8")
    (root / "404.html").write_text("<h1>Not found</h1>", encoding="utf-8")
    (root / "auth" / "login" / "index.html").write_text(
        "<h1>Sign in</h1>",
        encoding="utf-8",
    )
    (root / "auth" / "callback" / "index.html").write_text(
        "<h1>OAuth callback</h1>",
        encoding="utf-8",
    )
    (root / "_next" / "static" / "app.js").write_text(
        "console.log('static')",
        encoding="utf-8",
    )


def test_server_hosts_exported_web_without_swallowing_protocol_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACK_ANYWHERE_DATABASE_URL", raising=False)
    _write_static_export(tmp_path)
    client = TestClient(
        create_server(
            public_base_url="http://testserver",
            static_directory=tmp_path,
        )
    )

    home = client.get("/")
    login = client.get("/auth/login?next=%2F")
    asset = client.get("/_next/static/app.js")
    health = client.get("/api/v2/health")
    missing_api = client.get("/api/v2/does-not-exist")
    discovery = client.get("/.well-known/oauth-authorization-server")
    mcp = client.get("/mcp")

    assert home.status_code == 200
    assert "Track Anywhere" in home.text
    assert home.headers["cache-control"] == "no-cache"
    assert login.status_code == 200
    assert "Sign in" in login.text
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert health.status_code == 200
    assert health.headers["content-type"].startswith("application/json")
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/json")
    assert discovery.status_code == 200
    assert discovery.headers["content-type"].startswith("application/json")
    assert mcp.status_code == 503
    assert mcp.headers["content-type"].startswith("application/json")


def test_exported_route_does_not_redirect_or_downgrade_oauth_callback(
    tmp_path: Path,
) -> None:
    _write_static_export(tmp_path)
    client = TestClient(
        create_server(
            public_base_url="https://ledger.example.com",
            static_directory=tmp_path,
        )
    )

    response = client.get(
        "/auth/callback?code=secret",
        headers={
            "host": "ledger.example.com",
            "x-forwarded-for": "203.0.113.10",
            "x-forwarded-proto": "https",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "OAuth callback" in response.text
    assert "location" not in response.headers


def test_missing_hashed_asset_is_not_cached_as_immutable(tmp_path: Path) -> None:
    _write_static_export(tmp_path)
    client = TestClient(
        create_server(
            public_base_url="https://ledger.example.com",
            static_directory=tmp_path,
        )
    )

    response = client.get("/_next/static/missing.js")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-cache"


def test_static_web_is_get_only(tmp_path: Path) -> None:
    _write_static_export(tmp_path)
    client = TestClient(
        create_server(
            public_base_url="http://testserver",
            static_directory=tmp_path,
        )
    )

    response = client.post("/")

    assert response.status_code in {404, 405}
    assert response.headers["content-type"].startswith("application/json")


def test_configured_static_export_requires_an_index(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="index.html"):
        create_server(
            public_base_url="http://testserver",
            static_directory=tmp_path,
        )


def test_static_export_can_be_configured_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_static_export(tmp_path)
    monkeypatch.setenv("TRACK_ANYWHERE_STATIC_DIRECTORY", str(tmp_path))

    client = TestClient(create_server(public_base_url="http://testserver"))

    assert client.get("/").status_code == 200
