from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backend.tests.v2.backfill.test_run_pipeline import _synthetic_rows
from backend.tools.backfill_v1.extract import extract_canonical_rows
from backend.tools.backfill_v1.load import BackfillChangedSourceError
from backend.tools.backfill_v1.manifest import build_manifest, write_manifest
from backend.tools.backfill_v1.pipeline import (
    BackfillMappingError,
    load_extracted_rows_to_target,
    run_backfill,
)
import backend.tools.backfill_v1.pipeline as pipeline_module


@pytest.mark.parametrize(
    ("field", "meaningful_value"),
    [
        pytest.param("counterparty_id", "counterparty-1", id="counterparty"),
        pytest.param("project_id", "project-1", id="project"),
        pytest.param("necessity", "necessary", id="necessity"),
        pytest.param(
            "reimbursement_status",
            "pending",
            id="reimbursement-status",
        ),
    ],
)
def test_unsupported_v1_reporting_metadata_fails_before_any_target_write(
    migrated_postgres_database,
    tmp_path: Path,
    field: str,
    meaningful_value: str,
) -> None:
    rows = _synthetic_rows()
    rows["transaction_lines"][0][field] = meaningful_value
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / f"unsupported-{field}-extraction",
        dump_sha256="7" * 64,
        source_revision="v1-synthetic",
    )

    with pytest.raises(
        BackfillMappingError,
        match="unsupported_reporting_metadata",
    ):
        load_extracted_rows_to_target(
            target_url=migrated_postgres_database.runtime_url,
            manifest=manifest,
            rows_by_table=rows,
        )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(text("select count(*) from users"))) == 0
            assert int(connection.scalar(text("select count(*) from books"))) == 0
            assert (
                int(
                    connection.scalar(
                        text("select count(*) from backfill_source_receipts")
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


def test_resume_rejects_changed_dump_only_manifest_before_target_access(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "run"
    extract_canonical_rows(
        rows_by_table={},
        output_dir=output_dir / "extraction",
        dump_sha256="a" * 64,
        source_revision="v1-original",
    )
    dump_path = tmp_path / "changed.dump"
    dump_path.write_bytes(b"not the original dump")
    frozen_path = tmp_path / "dump-only-manifest.json"
    write_manifest(
        build_manifest(
            dump_sha256=hashlib.sha256(dump_path.read_bytes()).hexdigest(),
            source_revision="v1-changed",
            tables=(),
        ),
        frozen_path,
    )

    with pytest.raises(
        ValueError,
        match="extraction source identity does not match the frozen manifest",
    ):
        run_backfill(
            source_url="postgresql+psycopg://reader:x@127.0.0.1/source",
            target_url="postgresql+psycopg://writer:x@127.0.0.1/target",
            dump_path=dump_path,
            manifest_path=frozen_path,
            output_dir=output_dir,
            batch_size=10,
            workers=1,
            shuffle_seed=0,
        )


def test_resume_rejects_changed_dump_bytes_even_with_same_manifest(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "run"
    extracted = extract_canonical_rows(
        rows_by_table={},
        output_dir=output_dir / "extraction",
        dump_sha256="a" * 64,
        source_revision="v1-original",
    )
    frozen_path = tmp_path / "manifest.json"
    write_manifest(
        build_manifest(
            dump_sha256=extracted.dump_sha256,
            source_revision=extracted.source_revision,
            tables=(),
        ),
        frozen_path,
    )
    changed_dump = tmp_path / "changed.dump"
    changed_dump.write_bytes(b"different bytes")

    with pytest.raises(ValueError, match="frozen dump SHA-256"):
        run_backfill(
            source_url="postgresql+psycopg://reader:x@127.0.0.1/source",
            target_url="postgresql+psycopg://writer:x@127.0.0.1/target",
            dump_path=changed_dump,
            manifest_path=frozen_path,
            output_dir=output_dir,
            batch_size=10,
            workers=1,
            shuffle_seed=0,
        )


def test_foreign_snapshot_control_rows_fail_before_backfill_actor_or_business_write(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    manifest = extract_canonical_rows(
        rows_by_table={},
        output_dir=tmp_path / "foreign-control-extraction",
        dump_sha256="c" * 64,
        source_revision="v1-synthetic",
    )
    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into backfill_checkpoints "
                    "(snapshot_id, source_table, manifest_hash, "
                    "last_canonical_source_key, processed_count) "
                    "values ('sha256:foreign', 'accounts', :hash, '0001', 1)"
                ),
                {"hash": b"f" * 32},
            )
        with engine.connect() as connection:
            users_before = int(connection.scalar(text("select count(*) from users")))

        with pytest.raises(
            BackfillChangedSourceError,
            match="foreign backfill snapshot control state",
        ):
            load_extracted_rows_to_target(
                target_url=migrated_postgres_database.runtime_url,
                manifest=manifest,
                rows_by_table={},
            )

        with engine.connect() as connection:
            assert (
                int(connection.scalar(text("select count(*) from users")))
                == users_before
            )
            assert int(connection.scalar(text("select count(*) from books"))) == 0
    finally:
        engine.dispose()


def test_current_snapshot_checkpoint_manifest_mismatch_fails_before_write(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    manifest = extract_canonical_rows(
        rows_by_table={},
        output_dir=tmp_path / "changed-checkpoint-extraction",
        dump_sha256="d" * 64,
        source_revision="v1-synthetic",
    )
    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into backfill_checkpoints "
                    "(snapshot_id, source_table, manifest_hash, "
                    "last_canonical_source_key, processed_count) "
                    "values (:snapshot_id, 'accounts', :hash, '0001', 1)"
                ),
                {
                    "snapshot_id": manifest.snapshot_id,
                    "hash": b"x" * 32,
                },
            )

        with pytest.raises(
            BackfillChangedSourceError,
            match="checkpoint belongs to a different manifest",
        ):
            load_extracted_rows_to_target(
                target_url=migrated_postgres_database.runtime_url,
                manifest=manifest,
                rows_by_table={},
            )

        with engine.connect() as connection:
            assert int(connection.scalar(text("select count(*) from users"))) == 0
    finally:
        engine.dispose()


def test_mapping_failure_cannot_quarantine_a_nonempty_target_without_controls(
    migrated_postgres_database,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "run"
    invalid_rows = {
        "accounts": [
            {
                "account_id": "orphan-account",
                "book_id": "missing-book",
                "currency": "CNY",
                "institution": None,
                "institution_type": None,
                "name": "Orphan",
                "subtype": None,
                "type": "asset",
                "version": 1,
            }
        ]
    }
    dump_path = tmp_path / "snapshot.dump"
    dump_path.write_bytes(b"fixed snapshot")
    dump_hash = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    extracted = extract_canonical_rows(
        rows_by_table=invalid_rows,
        output_dir=output_dir / "extraction",
        dump_sha256=dump_hash,
        source_revision="v1-synthetic",
    )
    frozen_path = tmp_path / "manifest.json"
    write_manifest(
        build_manifest(
            dump_sha256=dump_hash,
            source_revision=extracted.source_revision,
            tables=(),
        ),
        frozen_path,
    )
    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into users "
                    "(user_id, subject_type, current_display_name, status) "
                    "values ('existing-user', 'human', 'Existing', 'active')"
                )
            )

        with pytest.raises(ValueError, match="target is not empty"):
            run_backfill(
                source_url="postgresql+psycopg://reader:x@127.0.0.1/source-other",
                target_url=migrated_postgres_database.runtime_url,
                dump_path=dump_path,
                manifest_path=frozen_path,
                output_dir=output_dir,
                batch_size=10,
                workers=1,
                shuffle_seed=0,
            )

        with engine.connect() as connection:
            assert int(connection.scalar(text("select count(*) from users"))) == 1
            assert (
                int(connection.scalar(text("select count(*) from backfill_quarantine")))
                == 0
            )
            assert int(connection.scalar(text("select count(*) from books"))) == 0
    finally:
        engine.dispose()


def test_first_source_failure_rolls_back_actor_with_the_source_receipt(
    migrated_postgres_database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _synthetic_rows()
    manifest = extract_canonical_rows(
        rows_by_table=rows,
        output_dir=tmp_path / "actor-atomicity-extraction",
        dump_sha256="f" * 64,
        source_revision="v1-synthetic",
    )

    def fail_create_book(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected first source failure")

    monkeypatch.setattr(pipeline_module, "create_book", fail_create_book)
    with pytest.raises(RuntimeError, match="first source failure"):
        load_extracted_rows_to_target(
            target_url=migrated_postgres_database.runtime_url,
            manifest=manifest,
            rows_by_table=rows,
        )

    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(text("select count(*) from users"))) == 0
            assert (
                int(
                    connection.scalar(
                        text("select count(*) from backfill_source_receipts")
                    )
                )
                == 0
            )
            assert int(connection.scalar(text("select count(*) from books"))) == 0
    finally:
        engine.dispose()
