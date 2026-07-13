from __future__ import annotations

from sqlalchemy import text


def test_postgres_17_is_the_integration_runtime(pg_engine, postgres_cluster_config) -> None:
    with pg_engine.connect() as connection:
        version = connection.execute(text("show server_version_num")).scalar_one()
        session_user = connection.execute(text("select session_user")).scalar_one()

    assert 170000 <= int(version) < 180000
    assert session_user == postgres_cluster_config.runtime_role
