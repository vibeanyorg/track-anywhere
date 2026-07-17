from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sys

from track_anywhere.application.imports.contracts import (
    canonical_plan_bytes,
    plan_summary,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes

from .credit_card_review import read_approved_credit_card_review
from .extract import extract_fixed_source
from .manifest import read_full_manifest
from .planner import compile_frozen_financial_history_plan


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if type(value) is not str or not value:
        raise ValueError("frozen planner environment is incomplete")
    return value


def _positive_environment_integer(
    environ: Mapping[str, str], name: str, *, default: int
) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("frozen planner scheduling is invalid") from None
    if value <= 0 or str(value) != raw:
        raise ValueError("frozen planner scheduling is invalid")
    return value


def _nonnegative_environment_integer(
    environ: Mapping[str, str], name: str, *, default: int
) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("frozen planner scheduling is invalid") from None
    if value < 0 or str(value) != raw:
        raise ValueError("frozen planner scheduling is invalid")
    return value


def _compile_from_environment(environ: Mapping[str, str]) -> tuple[bytes, bytes]:
    source_url = _required_environment(environ, "TRACK_ANYWHERE_FROZEN_SOURCE_URL")
    manifest_path = Path(
        _required_environment(environ, "TRACK_ANYWHERE_FROZEN_MANIFEST_PATH")
    )
    review_path = Path(
        _required_environment(environ, "TRACK_ANYWHERE_CREDIT_CARD_REVIEW_PATH")
    )
    manifest = read_full_manifest(manifest_path)
    source = extract_fixed_source(
        source_url,
        expected_manifest=manifest,
        batch_size=_positive_environment_integer(
            environ,
            "TRACK_ANYWHERE_FROZEN_BATCH_SIZE",
            default=256,
        ),
        workers=_positive_environment_integer(
            environ,
            "TRACK_ANYWHERE_FROZEN_WORKERS",
            default=1,
        ),
        shuffle_seed=_nonnegative_environment_integer(
            environ,
            "TRACK_ANYWHERE_FROZEN_SHUFFLE_SEED",
            default=0,
        ),
    )
    review = read_approved_credit_card_review(review_path, source=source)
    plan = compile_frozen_financial_history_plan(source=source, review=review)
    plan_bytes = canonical_plan_bytes(plan)
    summary_bytes = canonical_json_bytes(plan_summary(plan)) + b"\n"  # type: ignore[arg-type]
    return plan_bytes, summary_bytes


def main() -> int:
    try:
        plan_bytes, summary_bytes = _compile_from_environment(os.environ)
    except (KeyError, OSError, OverflowError, TypeError, UnicodeError, ValueError):
        sys.stderr.write('{"error":"plan_compilation_failed"}\n')
        sys.stderr.flush()
        return 2
    sys.stdout.buffer.write(plan_bytes)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(summary_bytes)
    sys.stderr.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    raise SystemExit(main())


__all__ = ["main"]
