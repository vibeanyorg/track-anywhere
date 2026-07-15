from __future__ import annotations

from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/verify-v2.sh"


def test_verify_v2_is_an_executable_strict_local_gate() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    for variable in (
        "TRACK_ANYWHERE_TEST_POSTGRES_ADMIN_URL",
        "TRACK_ANYWHERE_TEST_POSTGRES_MIGRATOR_BASE_URL",
        "TRACK_ANYWHERE_TEST_POSTGRES_RUNTIME_BASE_URL",
    ):
        assert f'${{{variable}:?required isolated PG17' in source
    assert "export TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE=1" in source
    assert "unset TRACK_ANYWHERE_TEST_POSTGRES_URL TRACK_ANYWHERE_DATABASE_URL" in source
    assert "backend/tests/v2/postgres_factory.py create" in source
    assert "--emit-role migrator" in source
    assert "role-name --kind runtime" in source
    assert "alembic upgrade head" in source
    assert "alembic check" in source
    assert "postgres_factory.py drop" in source

    forbidden_external_actions = (
        "railway ",
        "kubectl ",
        "docker push",
        "deploy-vps.sh",
        "stable-smoke.sh",
        "frozen_dump.sqlite",
    )
    assert not any(command in source for command in forbidden_external_actions)


def test_verify_v2_collects_each_required_v2_lane_without_legacy_tests() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    required_fragments = (
        "uv sync --locked --extra postgres",
        "pytest backend/tests/v2/unit -q",
        "pytest backend/tests/v2/postgres backend/tests/v2/concurrency -q",
        "pytest backend/tests/v2/replay -q",
        "pytest backend/tests/v2/contract cli/tests contract_tests -q",
        "npm --prefix frontend ci",
        "npm --prefix frontend run lint",
        "npm --prefix frontend run build",
    )
    for fragment in required_fragments:
        assert fragment in source
    assert "pytest backend/tests/test_" not in source
    assert "pytest backend/tests -q" not in source
    assert "backend/tests/v2/backfill" not in source
    assert "frozen_dump" not in source
