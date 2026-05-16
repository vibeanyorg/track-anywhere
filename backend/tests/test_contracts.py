from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from track_anywhere.api import app


def test_public_api_v1_route_snapshot():
    assert app is not None
    actual = {
        "paths": {
            path: sorted(method for method in details if method in {"get", "post", "put", "patch", "delete"})
            for path, details in sorted(app.openapi()["paths"].items())
        }
    }
    expected = json.loads((Path(__file__).parent / "snapshots" / "public-api-v1.json").read_text())

    assert actual == expected
