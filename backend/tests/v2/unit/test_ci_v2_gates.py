from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github" / "workflows" / "docker-image.yml"


def _job_block(source: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>(?:^(?:    |\s*$).*\n?)*)",
        source,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing CI job: {name}"
    return match.group("body")


def test_ci_requires_every_v2_gate_before_both_image_channels() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "postgres:17" in source
    assert "actions/setup-node@v4" in source
    assert 'node-version: "22"' in source
    assert "backend/tests/v2/unit" in source
    assert "backend/tests/v2/postgres" in source
    assert "backend/tests/v2/concurrency" in source
    assert "backend/tests/v2/replay" in source
    assert "backend/tests/v2/contract cli/tests contract_tests" in source
    assert "--emit-role migrator" in source
    assert "role-name --kind runtime" in source
    assert "alembic upgrade head" in source
    assert "alembic check" in source
    assert "npm --prefix frontend ci" in source
    assert "npm --prefix frontend run lint" in source
    assert "npm --prefix frontend run build" in source
    assert "backend/tests/v2/backfill" not in source
    assert "frozen_dump" not in source
    assert source.index("actions/setup-node@v4") < source.index(
        "npm --prefix frontend ci"
    )

    expected_gates = {"v2-gates", "hash-vectors", "docker-postgres-e2e"}
    for build_job in ("build-nightly", "build-stable"):
        block = _job_block(source, build_job)
        needs = set(
            re.findall(
                r"^      - (v2-gates|hash-vectors|docker-postgres-e2e)$",
                block,
                re.MULTILINE,
            )
        )
        assert needs == expected_gates
    assert re.search(r"^  [^\n]*deploy[^\n]*:", source, re.MULTILINE) is None


def test_hash_vector_matrix_claims_only_python_312_and_313() -> None:
    block = _job_block(WORKFLOW.read_text(encoding="utf-8"), "hash-vectors")
    assert 'python-version: ["3.12", "3.13"]' in block
