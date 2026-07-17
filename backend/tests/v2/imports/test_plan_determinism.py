from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from track_anywhere.application.imports.contracts import canonical_plan_bytes

from backend.tests.v2.imports.test_plan_archive import approved_plan


def test_fixed_plan_is_identical_across_extraction_scheduling() -> None:
    first = approved_plan(batch_size=37, workers=1, seed=0)
    second = approved_plan(batch_size=13, workers=4, seed=731)

    assert canonical_plan_bytes(first) == canonical_plan_bytes(second)


def _run_cli_with_scheduling(
    *, tz: str, locale: str, hash_seed: str, batch: str, workers: str, seed: str
) -> tuple[bytes, dict[str, object]]:
    required = (
        "TRACK_ANYWHERE_FROZEN_SOURCE_URL",
        "TRACK_ANYWHERE_FROZEN_MANIFEST_PATH",
        "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH",
    )
    if any(not os.getenv(name) for name in required):
        pytest.skip("fixed restored source artifacts are not configured")
    environment = os.environ.copy()
    environment.update(
        {
            "TZ": tz,
            "LC_ALL": locale,
            "PYTHONHASHSEED": hash_seed,
            "TRACK_ANYWHERE_FROZEN_BATCH_SIZE": batch,
            "TRACK_ANYWHERE_FROZEN_WORKERS": workers,
            "TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED": seed,
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "backend.tools.frozen_v1_history"],
        cwd=Path(__file__).parents[4],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"frozen planner subprocess failed with code {result.returncode}",
            pytrace=False,
        )
    try:
        summary = json.loads(result.stderr)
    except (TypeError, ValueError):
        pytest.fail(
            "frozen planner subprocess emitted an invalid summary", pytrace=False
        )
    if type(summary) is not dict:
        pytest.fail(
            "frozen planner subprocess emitted an invalid summary", pytrace=False
        )
    return result.stdout, summary


def test_fixed_plan_cli_is_byte_identical_across_process_environment() -> None:
    first, first_summary = _run_cli_with_scheduling(
        tz="UTC",
        locale="C",
        hash_seed="1",
        batch="37",
        workers="1",
        seed="0",
    )
    second, second_summary = _run_cli_with_scheduling(
        tz="Asia/Shanghai",
        locale="C.UTF-8",
        hash_seed="731",
        batch="13",
        workers="4",
        seed="731",
    )

    assert len(first) == len(second)
    assert hmac.compare_digest(
        hashlib.sha256(first).digest(),
        hashlib.sha256(second).digest(),
    )
    assert first_summary == second_summary
