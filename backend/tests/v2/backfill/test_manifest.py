from __future__ import annotations

import hashlib

import pytest

from backend.tools.backfill_v1.config import BackfillConfig
from backend.tools.backfill_v1.config import current_v2_head
from backend.tools.backfill_v1.manifest import (
    FrozenSourceManifest,
    assert_target_ready,
    read_manifest,
    validate_target_state,
    verify_frozen_source,
)


def test_source_and_target_must_be_different_databases(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"frozen")

    with pytest.raises(ValueError, match="different PostgreSQL databases"):
        BackfillConfig(
            source_url="postgresql+psycopg://reader:a@127.0.0.1:15543/frozen",
            target_url="postgresql+psycopg://writer:b@localhost:15543/frozen",
            dump_path=dump,
            source_revision="v1-final",
            output_dir=tmp_path / "output",
        )


def test_frozen_dump_hash_and_revision_must_match(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"immutable-v1-snapshot")
    manifest = FrozenSourceManifest(
        format_version=1,
        dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest(),
        source_revision="v1-final",
        snapshot_id="snapshot-fixture",
        tables=(),
    )

    verify_frozen_source(
        dump_path=dump,
        manifest=manifest,
        actual_source_revision="v1-final",
    )

    dump.write_bytes(b"changed")
    with pytest.raises(ValueError, match="dump SHA-256"):
        verify_frozen_source(
            dump_path=dump,
            manifest=manifest,
            actual_source_revision="v1-final",
        )

    dump.write_bytes(b"immutable-v1-snapshot")
    with pytest.raises(ValueError, match="source revision"):
        verify_frozen_source(
            dump_path=dump,
            manifest=manifest,
            actual_source_revision="another-revision",
        )


def test_fixed_backup_manifest_accepts_source_alembic_revision(tmp_path) -> None:
    manifest_path = tmp_path / "fixed-backup.manifest.txt"
    manifest_path.write_text(
        "\n".join(
            (
                "sha256=" + "a" * 64,
                "source_alembic_revision=0019_posting_constraints",
                "accounts=121",
                "transactions=135",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = read_manifest(manifest_path)

    assert manifest.dump_sha256 == "a" * 64
    assert manifest.source_revision == "0019_posting_constraints"


def test_target_requires_exact_head_and_zero_backfill_business_rows() -> None:
    validate_target_state(
        actual_revision="v2-head",
        expected_revision="v2-head",
        row_counts={
            "v2_schema_metadata": 1,
            "synchronous_projection_event_types": 9,
            "projection_generations": 1,
            "books": 0,
            "ledger_events": 0,
            "command_receipts": 0,
            "backfill_quarantine": 0,
            "backfill_seals": 0,
        },
    )

    with pytest.raises(ValueError, match="exact V2 Alembic head"):
        validate_target_state(
            actual_revision="old-head",
            expected_revision="v2-head",
            row_counts={},
        )

    with pytest.raises(ValueError, match="target is not empty.*books=1"):
        validate_target_state(
            actual_revision="v2-head",
            expected_revision="v2-head",
            row_counts={"books": 1, "projection_generations": 1},
        )


def test_fresh_migrated_target_passes_exact_head_and_empty_gate(
    migrated_postgres_database,
) -> None:
    assert_target_ready(
        migrated_postgres_database.runtime_url,
        expected_revision=current_v2_head(),
    )
