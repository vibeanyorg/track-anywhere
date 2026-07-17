from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import re
import sys
from typing import BinaryIO, Final
from uuid import UUID

from track_anywhere.application.imports.contracts import (
    canonical_plan_bytes,
    parse_canonical_plan_bytes,
    plan_sha256,
    plan_summary,
)
from track_anywhere.serialization.canonical_json import canonical_json_bytes

from .constants import (
    EXPECTED_CREDIT_CARD_REVIEW_SHA256,
    EXPECTED_FULL_MANIFEST_SHA256,
)
from .credit_card_review import (
    _read_strict_json,
    calculated_review_sha256,
    read_approved_credit_card_review,
)
from .extract import extract_fixed_source
from .manifest import read_full_manifest
from .planner import compile_frozen_financial_history_plan
from .reference_artifact import (
    COUNT_ALLOWLIST,
    HASH_ALLOWLIST,
    serialize_reference_artifact,
)
from .reference_reducer import bind_source_reference, reduce_canonical_plan
from .verify import reduce_approved_source_reference


EXPECTED_PLAN_SHA256: Final = (
    "c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8"
)
EXPECTED_SOURCE_DUMP_SHA256: Final = (
    "a125b857a317e8c017d7028a26f78cf664ff6d57f6c0e698b3c229acf5d6cf9e"
)
EXPECTED_TERMINAL_HASH: Final = (
    "bcc2828422fda617df93fb2fc92e41599f0c694f9f1d502f1dcd22f4d85186fc"
)
EXPECTED_CATALOG_IDENTITY_SHA256: Final = (
    "3b7556099f961ffdd65869fd2cd41af97aa0360406586734fab0cd71bce2dc02"
)
EXPECTED_ALEMBIC_VERSION: Final = "v2_0013_frozen_import_fence"
TARGET_BOOK_ID: Final = UUID("a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d")
MAX_REFERENCE_PLAN_BYTES: Final = 8 * 1024 * 1024
MAX_REPORT_BYTES: Final = 64 * 1024
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$", flags=re.ASCII)
_REPORT_KEYS: Final = frozenset(
    {
        "alembic_version",
        "archive_sha256",
        "balance_sha256",
        "candidate_image_id",
        "catalog_identity_sha256",
        "catalog_sha256",
        "counts",
        "credit_card_review_sha256",
        "description_plaintext_sha256",
        "deterministic_ids_sha256",
        "event_order_sha256",
        "event_payloads_sha256",
        "plan_sha256",
        "postgres_version_num",
        "projection_sha256",
        "quarantine_count",
        "receipt_state",
        "resource_counts",
        "role_names",
        "run_id",
        "source_commit",
        "source_dump_bytes",
        "source_dump_sha256",
        "source_manifest_sha256",
        "status",
        "terminal_hash",
    }
)
_REPORT_DIGEST_KEYS: Final = _REPORT_KEYS - {
    "candidate_image_id",
    "alembic_version",
    "counts",
    "postgres_version_num",
    "quarantine_count",
    "receipt_state",
    "resource_counts",
    "role_names",
    "run_id",
    "source_commit",
    "source_dump_bytes",
    "status",
}


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


def _run_planner() -> int:
    try:
        plan_bytes, summary_bytes = _compile_from_environment(os.environ)
    except Exception:
        sys.stderr.write('{"error":"plan_compilation_failed"}\n')
        sys.stderr.flush()
        return 2
    sys.stdout.buffer.write(plan_bytes)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(summary_bytes)
    sys.stderr.buffer.flush()
    return 0


def _read_reference_plan(stdin: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_REFERENCE_PLAN_BYTES:
        try:
            chunk = stdin.read(MAX_REFERENCE_PLAN_BYTES + 1 - total)
        except Exception:
            raise ValueError("reference_stdin_read_failed") from None
        if type(chunk) is not bytes:
            raise ValueError("reference_stdin_read_failed")
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_REFERENCE_PLAN_BYTES:
            raise ValueError("reference_stdin_too_large")
    raise ValueError("reference_stdin_too_large")


def _compile_reference_from_environment(
    raw: bytes,
    environ: Mapping[str, str],
) -> bytes:
    plan = parse_canonical_plan_bytes(raw)
    if (
        plan.target_book_id != TARGET_BOOK_ID
        or plan_sha256(plan) != EXPECTED_PLAN_SHA256
    ):
        raise ValueError("reference_plan_contract_mismatch")

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
    source_facts = reduce_approved_source_reference(
        source=source,
        review=review,
        target_book_id=TARGET_BOOK_ID,
    )
    raw_plan = json.loads(canonical_plan_bytes(plan))
    if type(raw_plan) is not dict:
        raise ValueError("reference_plan_contract_mismatch")
    auxiliary = reduce_canonical_plan(raw_plan)
    reference = bind_source_reference(auxiliary, source_facts)
    artifact = serialize_reference_artifact(reference)
    del source, review, source_facts, raw_plan, auxiliary, reference, plan
    return artifact


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("rehearsal_report_invalid")
        result[key] = value
    return result


def _exact_integer_mapping(
    value: object,
    expected: Mapping[str, int],
) -> bool:
    return (
        type(value) is dict
        and set(value) == set(expected)
        and all(
            type(value[key]) is int and value[key] == expected[key] for key in expected
        )
    )


def _verify_report(path: Path) -> dict[str, str]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_REPORT_BYTES or not raw.endswith(b"\n"):
            raise ValueError
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
        if type(parsed) is not dict or set(parsed) != _REPORT_KEYS:
            raise ValueError
        if canonical_json_bytes(parsed) + b"\n" != raw:  # type: ignore[arg-type]
            raise ValueError
        if parsed.get("status") != "PASS":
            raise ValueError
        for key in _REPORT_DIGEST_KEYS:
            value = parsed.get(key)
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ValueError
        image_id = parsed.get("candidate_image_id")
        if (
            type(image_id) is not str
            or not image_id.startswith("sha256:")
            or _SHA256.fullmatch(image_id.removeprefix("sha256:")) is None
        ):
            raise ValueError
        source_commit = parsed.get("source_commit")
        if type(source_commit) is not str or _COMMIT.fullmatch(source_commit) is None:
            raise ValueError
        run_id = parsed.get("run_id")
        if type(run_id) is not str or str(UUID(run_id)) != run_id:
            raise ValueError
        if (
            parsed.get("source_dump_bytes") != 193256
            or parsed.get("source_dump_sha256") != EXPECTED_SOURCE_DUMP_SHA256
        ):
            raise ValueError
        if (
            parsed.get("plan_sha256") != EXPECTED_PLAN_SHA256
            or parsed.get("terminal_hash") != EXPECTED_TERMINAL_HASH
            or parsed.get("catalog_identity_sha256") != EXPECTED_CATALOG_IDENTITY_SHA256
            or parsed.get("source_manifest_sha256") != EXPECTED_FULL_MANIFEST_SHA256
            or parsed.get("credit_card_review_sha256")
            != EXPECTED_CREDIT_CARD_REVIEW_SHA256
        ):
            raise ValueError
        resources = parsed.get("resource_counts")
        if not _exact_integer_mapping(
            resources,
            {"containers": 0, "networks": 0, "volumes": 0},
        ):
            raise ValueError
        postgres_version_num = parsed.get("postgres_version_num")
        if (
            type(postgres_version_num) is not int
            or postgres_version_num < 170000
            or postgres_version_num >= 180000
        ):
            raise ValueError
        if parsed.get("alembic_version") != EXPECTED_ALEMBIC_VERSION:
            raise ValueError
        if not _exact_integer_mapping(parsed.get("counts"), COUNT_ALLOWLIST):
            raise ValueError
        quarantine_count = parsed.get("quarantine_count")
        if type(quarantine_count) is not int or quarantine_count != 0:
            raise ValueError
        receipt_state = parsed.get("receipt_state")
        if (
            type(receipt_state) is not dict
            or set(receipt_state)
            != {
                "first_apply",
                "first_apply_replayed",
                "replay",
                "replay_inserted_total",
                "replayed",
            }
            or receipt_state.get("first_apply") != "completed"
            or receipt_state.get("first_apply_replayed") is not False
            or receipt_state.get("replay") != "completed"
            or type(receipt_state.get("replay_inserted_total")) is not int
            or receipt_state.get("replay_inserted_total") != 0
            or receipt_state.get("replayed") is not True
        ):
            raise ValueError
        if parsed.get("role_names") != {
            "migrator": "frozen_migrator",
            "owner": "frozen_owner",
            "runtime": "frozen_runtime",
            "source_reader": "frozen_source_reader",
        }:
            raise ValueError
        return {"status": "PASS"}
    except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("rehearsal_report_invalid") from None


def _verify_review_content(path: Path) -> dict[str, str]:
    try:
        parsed = _read_strict_json(path)
        if type(parsed) is not dict:
            raise ValueError
        embedded = parsed.get("content_sha256")
        calculated = calculated_review_sha256(parsed)
        if (
            embedded != EXPECTED_CREDIT_CARD_REVIEW_SHA256
            or calculated != EXPECTED_CREDIT_CARD_REVIEW_SHA256
        ):
            raise ValueError
        return {"content_sha256": calculated, "status": "PASS"}
    except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("approved_review_invalid") from None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = () if argv is None else tuple(argv)
    if not arguments:
        return _run_planner()
    if arguments == ("reference", "--stdin"):
        raw = b""
        try:
            raw = _read_reference_plan(sys.stdin.buffer)
            artifact = _compile_reference_from_environment(raw, os.environ)
        except Exception:
            sys.stderr.write('{"error":"reference_compilation_failed"}\n')
            sys.stderr.flush()
            return 2
        finally:
            raw = b""
        sys.stdout.buffer.write(artifact)
        sys.stdout.buffer.flush()
        summary = {"counts": 15, "hashes": len(HASH_ALLOWLIST), "status": "PASS"}
        sys.stderr.buffer.write(canonical_json_bytes(summary) + b"\n")
        sys.stderr.buffer.flush()
        return 0
    if len(arguments) == 2 and arguments[0] == "verify-report":
        try:
            result = _verify_report(Path(arguments[1]))
        except ValueError:
            sys.stderr.write('{"error":"rehearsal_report_invalid"}\n')
            sys.stderr.flush()
            return 2
        sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
        sys.stdout.buffer.flush()
        return 0
    if len(arguments) == 2 and arguments[0] == "verify-review-content":
        try:
            result = _verify_review_content(Path(arguments[1]))
        except ValueError:
            sys.stderr.write('{"error":"approved_review_invalid"}\n')
            sys.stderr.flush()
            return 2
        sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
        sys.stdout.buffer.flush()
        return 0
    sys.stderr.write('{"error":"invalid_arguments"}\n')
    sys.stderr.flush()
    return 2


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    raise SystemExit(main(tuple(sys.argv[1:])))


__all__ = ["main"]
