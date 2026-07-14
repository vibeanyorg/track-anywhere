from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from backend.tools.backfill_v1.load import (
    BackfillChangedSourceError,
    ResumableBackfillLoader,
    SourceLoadItem,
)
from track_anywhere.infrastructure.db.models.backfill import (
    BackfillCheckpointRecord,
    BackfillSourceReceiptRecord,
)
from track_anywhere.infrastructure.db.models.catalog import BookRecord


def _item(scenario, number: int, *, source_hash: bytes | None = None) -> SourceLoadItem:
    return SourceLoadItem(
        source_table="accounts",
        source_primary_key=f"account-{number}",
        canonical_source_key=f"{number:04d}",
        source_hash=source_hash
        or hashlib.sha256(f"account-{number}".encode()).digest(),
        book_id=scenario.book_id,
        target_entity_id=None,
        payload={"suffix": str(number)},
    )


def _apply_name_marker(session: Session, item: SourceLoadItem) -> None:
    book = session.get(BookRecord, item.book_id)
    assert book is not None
    book.current_name += f":{item.payload['suffix']}"
    session.flush([book])


def test_kill_resume_receipts_checkpoint_and_book_effect_are_atomic(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    items = tuple(_item(scenario, number) for number in (1, 2, 3))

    def crash(item: SourceLoadItem) -> None:
        if item.source_primary_key == "account-2":
            raise RuntimeError("injected process termination")

    loader = ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-a",
        manifest_hash=b"m" * 32,
        apply_item=_apply_name_marker,
        after_apply_before_receipt=crash,
    )
    with pytest.raises(RuntimeError, match="termination"):
        loader.load(items)

    with Session(pg_engine) as session:
        assert session.get(BookRecord, scenario.book_id).current_name.endswith(":1")
        assert (
            session.scalar(
                select(func.count()).select_from(BackfillSourceReceiptRecord)
            )
            == 1
        )
        checkpoint = session.get(
            BackfillCheckpointRecord,
            ("sha256:snapshot-a", "accounts"),
        )
        assert checkpoint is not None
        assert (checkpoint.last_canonical_source_key, checkpoint.processed_count) == (
            "0001",
            1,
        )

    resumed = ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-a",
        manifest_hash=b"m" * 32,
        apply_item=_apply_name_marker,
    )
    result = resumed.load(items)
    replay = resumed.load(items)

    assert (result.applied, result.replayed) == (2, 1)
    assert (replay.applied, replay.replayed) == (0, 3)
    with Session(pg_engine) as session:
        assert session.get(BookRecord, scenario.book_id).current_name.endswith(":1:2:3")
        assert (
            session.scalar(
                select(func.count()).select_from(BackfillSourceReceiptRecord)
            )
            == 3
        )


def test_changed_manifest_or_duplicate_source_content_is_rejected(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    item = _item(scenario, 1)
    ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-a",
        manifest_hash=b"m" * 32,
        apply_item=_apply_name_marker,
    ).load((item,))

    changed_manifest = ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-a",
        manifest_hash=b"n" * 32,
        apply_item=_apply_name_marker,
    )
    with pytest.raises(BackfillChangedSourceError, match="different manifest"):
        changed_manifest.load((item,))

    changed_item = _item(scenario, 1, source_hash=b"x" * 32)
    same_manifest = ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-a",
        manifest_hash=b"m" * 32,
        apply_item=_apply_name_marker,
    )
    with pytest.raises(BackfillChangedSourceError, match="different content"):
        same_manifest.load((changed_item,))


def test_database_rejects_checkpoint_regression_and_receipt_mutation(pg_engine) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-constraints",
        manifest_hash=b"c" * 32,
        apply_item=_apply_name_marker,
    ).load((_item(scenario, 1), _item(scenario, 2)))

    with pytest.raises(SQLAlchemyError):
        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    "update backfill_checkpoints "
                    "set last_canonical_source_key = '0001', processed_count = 1 "
                    "where snapshot_id = 'sha256:snapshot-constraints'"
                )
            )
    with pytest.raises(SQLAlchemyError):
        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    "update backfill_source_receipts set source_hash = :hash "
                    "where snapshot_id = 'sha256:snapshot-constraints'"
                ),
                {"hash": b"x" * 32},
            )
