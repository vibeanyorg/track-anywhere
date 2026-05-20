from __future__ import annotations

import os

import pytest

from .api_clients import BackendApiClient, FastApiClient


os.environ.setdefault("TRACK_ANYWHERE_DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
def backend_client() -> BackendApiClient:
    return FastApiClient()
