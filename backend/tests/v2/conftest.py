from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine

from backend.tests.v2.postgres_factory import (
    ClusterConfig,
    PostgresDatabaseFactory,
    ProvisionedDatabase,
)


@pytest.fixture
def postgres_cluster_config() -> ClusterConfig:
    try:
        return ClusterConfig.from_env()
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)


@pytest.fixture
def postgres_database_factory() -> Iterator[PostgresDatabaseFactory]:
    try:
        config = ClusterConfig.from_env()
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)

    factory = PostgresDatabaseFactory(
        config,
        worker_id=os.environ.get("PYTEST_XDIST_WORKER", "main"),
        test_uuid=uuid.uuid4().hex,
    )
    try:
        yield factory
    finally:
        factory.close()


@pytest.fixture
def empty_postgres_database(postgres_database_factory: PostgresDatabaseFactory) -> ProvisionedDatabase:
    return postgres_database_factory.create(purpose="runtime")


@pytest.fixture
def empty_postgres_source_target(
    postgres_database_factory: PostgresDatabaseFactory,
) -> tuple[ProvisionedDatabase, ProvisionedDatabase]:
    return (
        postgres_database_factory.create(purpose="source"),
        postgres_database_factory.create(purpose="target"),
    )


@pytest.fixture
def pg_engine(
    empty_postgres_database: ProvisionedDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    monkeypatch.setenv("TRACK_ANYWHERE_TEST_POSTGRES_URL", empty_postgres_database.runtime_url)
    monkeypatch.setenv("TRACK_ANYWHERE_DATABASE_URL", empty_postgres_database.runtime_url)
    engine = create_engine(empty_postgres_database.runtime_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
