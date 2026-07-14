from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from backend.tests.v2.postgres_factory import (
    ClusterConfig,
    PostgresDatabaseFactory,
)

from .api_clients import BackendApiClient, FastApiClient


@pytest.fixture
def backend_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[BackendApiClient]:
    config = ClusterConfig.from_env()
    factory = PostgresDatabaseFactory(
        config,
        worker_id=os.environ.get("PYTEST_XDIST_WORKER", "main"),
        test_uuid=uuid.uuid4().hex,
    )
    database = factory.create(purpose="contract", schema="v2")
    monkeypatch.setenv("TRACK_ANYWHERE_DATABASE_URL", database.runtime_url)
    monkeypatch.setenv("TRACK_ANYWHERE_DB_RUNTIME_ROLE", database.runtime_role)
    monkeypatch.setenv("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", "0")

    client = FastApiClient(
        database.runtime_url,
        expected_runtime_role=database.runtime_role,
    )
    try:
        yield client
    finally:
        client.close()
        factory.close()
