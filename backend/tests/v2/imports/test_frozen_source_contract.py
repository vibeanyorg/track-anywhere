from __future__ import annotations

from pathlib import Path
import json
import os

import pytest

from backend.tools.frozen_v1_history.constants import (
    EXPECTED_DUMP_BYTES,
    EXPECTED_DUMP_SHA256,
    EXPECTED_FULL_MANIFEST_SHA256,
    EXPECTED_SIMPLE_MANIFEST_COUNTS,
    EXPECTED_SOURCE_RECEIPTS,
    EXPECTED_SOURCE_REVISION,
    EXPECTED_SOURCE_TABLE_COUNTS,
    FROZEN_SOURCE_ARTIFACT,
)
from backend.tools.frozen_v1_history.manifest import (
    assert_approved_manifest,
    canonical_json_bytes,
    read_full_manifest,
    read_simple_manifest,
    sha256_file,
)


FIXTURES = Path(__file__).with_name("fixtures")


def _simple_manifest_text(**overrides: str) -> str:
    values = {
        "artifact": FROZEN_SOURCE_ARTIFACT,
        "created_at": "2026-07-13T01:56:34Z",
        "verified_at": "2026-07-13T02:00:26Z",
        "sha256": EXPECTED_DUMP_SHA256,
        "bytes": str(EXPECTED_DUMP_BYTES),
        "archive_entries": "140",
        "source_database": "track_anywhere",
        "source_schema": "public",
        "source_runtime_revision": "ed52ac2",
        "source_alembic_revision": EXPECTED_SOURCE_REVISION,
        **{name: str(count) for name, count in EXPECTED_SIMPLE_MANIFEST_COUNTS.items()},
        "restore_test": "passed",
        "restore_postgres_version": "17",
        "restore_database": "track_anywhere_restore",
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def test_fixed_constants_separate_source_rows_from_receipt_table_rows() -> None:
    assert EXPECTED_SOURCE_TABLE_COUNTS == {
        "accounts": 121,
        "assets": 20,
        "categories": 37,
        "category_versions": 37,
        "classification_events": 43,
        "counterparties": 2,
        "investment_events": 6,
        "investment_valuations": 0,
        "ledger_books": 1,
        "postings": 284,
        "transaction_lines": 43,
        "transactions": 135,
    }
    assert sum(EXPECTED_SOURCE_TABLE_COUNTS.values()) == EXPECTED_SOURCE_RECEIPTS == 729
    assert EXPECTED_SIMPLE_MANIFEST_COUNTS["idempotency_receipts"] == 238
    assert EXPECTED_SIMPLE_MANIFEST_COUNTS["idempotency_receipts"] != EXPECTED_SOURCE_RECEIPTS


def test_simple_manifest_accepts_only_the_fixed_source_contract(tmp_path: Path) -> None:
    path = tmp_path / "source.manifest.txt"
    path.write_text(_simple_manifest_text(), encoding="utf-8")

    manifest = read_simple_manifest(path)

    assert manifest.artifact == FROZEN_SOURCE_ARTIFACT
    assert manifest.dump_sha256 == EXPECTED_DUMP_SHA256
    assert manifest.dump_bytes == EXPECTED_DUMP_BYTES
    assert manifest.source_revision == EXPECTED_SOURCE_REVISION
    assert manifest.counts == EXPECTED_SIMPLE_MANIFEST_COUNTS


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"sha256": "0" * 64}, "fixed dump hash"),
        ({"bytes": "1"}, "fixed dump size"),
        ({"source_alembic_revision": "wrong"}, "source revision"),
        ({"transactions": "134"}, "fixed table count"),
        ({"created_at": "2026-07-13T01:56:35Z"}, "fixed metadata"),
        ({"verified_at": "2026-07-13T02:00:27Z"}, "fixed metadata"),
        ({"archive_entries": "139"}, "fixed metadata"),
        ({"source_database": "other"}, "fixed metadata"),
        ({"source_schema": "private"}, "fixed metadata"),
        ({"source_runtime_revision": "0000000"}, "fixed metadata"),
        ({"restore_test": "failed"}, "fixed metadata"),
        ({"restore_postgres_version": "16"}, "fixed metadata"),
        ({"restore_database": "other_restore"}, "fixed metadata"),
    ],
)
def test_simple_manifest_rejects_fixed_contract_mismatches(
    tmp_path: Path,
    mutation: dict[str, str],
    match: str,
) -> None:
    path = tmp_path / "source.manifest.txt"
    path.write_text(_simple_manifest_text(**mutation), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        read_simple_manifest(path)


def test_simple_manifest_metadata_mismatch_does_not_echo_input(tmp_path: Path) -> None:
    secret = "sensitive-metadata-must-not-escape"
    path = tmp_path / "source.manifest.txt"
    path.write_text(_simple_manifest_text(source_schema=secret), encoding="utf-8")

    with pytest.raises(ValueError, match="fixed metadata mismatch") as exc_info:
        read_simple_manifest(path)

    assert secret not in str(exc_info.value)


def test_simple_manifest_rejects_unknown_and_duplicate_keys_without_echoing_values(
    tmp_path: Path,
) -> None:
    secret = "sensitive-value-must-not-escape"
    path = tmp_path / "source.manifest.txt"
    path.write_text(_simple_manifest_text() + f"unknown={secret}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown manifest key") as exc_info:
        read_simple_manifest(path)

    assert secret not in str(exc_info.value)

    path.write_text(_simple_manifest_text() + "accounts=121\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate manifest key"):
        read_simple_manifest(path)


def test_sanitized_full_manifest_is_strict_and_hash_bound() -> None:
    manifest = read_full_manifest(FIXTURES / "frozen_full_manifest.json")

    assert manifest.content_sha256 == EXPECTED_FULL_MANIFEST_SHA256
    assert manifest.calculated_content_sha256() == EXPECTED_FULL_MANIFEST_SHA256
    assert manifest.snapshot_id == f"sha256:{EXPECTED_FULL_MANIFEST_SHA256}"
    assert {table.table: table.row_count for table in manifest.tables} == dict(
        EXPECTED_SOURCE_TABLE_COUNTS
    )


def test_full_manifest_rejects_unknown_duplicate_and_noncanonical_hashes(
    tmp_path: Path,
) -> None:
    raw = json.loads((FIXTURES / "frozen_full_manifest.json").read_text())
    raw["unexpected"] = "must-not-escape"
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical_json_bytes(raw))
    with pytest.raises(ValueError, match="fields do not match") as exc_info:
        read_full_manifest(path)
    assert "must-not-escape" not in str(exc_info.value)

    duplicate = b'{"format_version":1,"format_version":1}'
    path.write_bytes(duplicate)
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        read_full_manifest(path)

    raw.pop("unexpected")
    raw["tables"][0]["ndjson_sha256"] = "A" * 64
    path.write_bytes(canonical_json_bytes(raw))
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        read_full_manifest(path)


def test_full_manifest_rejects_table_drift_before_content_hash_drift(
    tmp_path: Path,
) -> None:
    raw = json.loads((FIXTURES / "frozen_full_manifest.json").read_text())
    raw["tables"][0]["row_count"] = 120
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical_json_bytes(raw))

    with pytest.raises(ValueError, match="fixed table count"):
        read_full_manifest(path)


def test_import_entry_rejects_a_hand_built_unpinned_manifest() -> None:
    approved = read_full_manifest(FIXTURES / "frozen_full_manifest.json")
    drifted = type(approved)(
        format_version=approved.format_version,
        dump_sha256=approved.dump_sha256,
        source_revision=approved.source_revision,
        snapshot_id="sha256:" + "0" * 64,
        tables=approved.tables,
        content_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="approved frozen manifest"):
        assert_approved_manifest(drifted)


@pytest.mark.skipif(
    not os.getenv("TRACK_ANYWHERE_FROZEN_V1_DUMP_PATH"),
    reason="fixed V1 artifacts are verified on the DO rehearsal host",
)
def test_real_fixed_dump_and_a_b_manifests_match_byte_for_byte() -> None:
    dump = Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_DUMP_PATH"])
    manifest_a = Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_MANIFEST_A"])
    manifest_b = Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_MANIFEST_B"])

    assert sha256_file(dump) == EXPECTED_DUMP_SHA256
    assert manifest_a.read_bytes() == manifest_b.read_bytes()
    assert read_full_manifest(manifest_a) == read_full_manifest(manifest_b)
