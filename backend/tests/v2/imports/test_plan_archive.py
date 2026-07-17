from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pytest

from backend.tools.frozen_v1_history.credit_card_review import (
    read_approved_credit_card_review,
)
from backend.tools.frozen_v1_history.extract import extract_fixed_source
from backend.tools.frozen_v1_history.manifest import read_full_manifest
from backend.tools.frozen_v1_history.planner import (
    compile_frozen_financial_history_plan,
)


@lru_cache(maxsize=8)
def approved_source_and_review(
    *, batch_size: int = 37, workers: int = 1, seed: int = 0
):
    source_url = os.getenv("TRACK_ANYWHERE_FROZEN_SOURCE_URL")
    manifest_path = os.getenv("TRACK_ANYWHERE_FROZEN_MANIFEST_PATH")
    review_path = os.getenv("TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH")
    if not source_url or not manifest_path or not review_path:
        pytest.skip("fixed restored source artifacts are not configured")
    manifest = read_full_manifest(Path(manifest_path))
    source = extract_fixed_source(
        source_url,
        expected_manifest=manifest,
        batch_size=batch_size,
        workers=workers,
        shuffle_seed=seed,
    )
    review = read_approved_credit_card_review(Path(review_path), source=source)
    return source, review


def approved_plan(*, batch_size: int = 37, workers: int = 1, seed: int = 0):
    source, review = approved_source_and_review(
        batch_size=batch_size,
        workers=workers,
        seed=seed,
    )
    return compile_frozen_financial_history_plan(source=source, review=review)


def test_fixed_archive_preserves_only_the_approved_unsupported_collections() -> None:
    plan = approved_plan()
    counts = plan.archive.record_counts

    assert counts is not None
    assert counts.classification_audit_records == 43
    assert counts.investment_activities == 6
    assert counts.investment_valuations == 0
    assert counts.uncategorized_fx_reporting_facts == 5
    assert counts.counterparty_records == 2
    assert counts.omission_records == 5
    assert plan.archive.kind == "import_archive"
