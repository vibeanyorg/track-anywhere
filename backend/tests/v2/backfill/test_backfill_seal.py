from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from backend.tools.backfill_v1.load import (
    BackfillChangedSourceError,
    ResumableBackfillLoader,
    SourceLoadItem,
    seal_backfill,
    write_verification,
)


def test_seal_is_idempotent_immutable_and_writes_stable_verification(
    pg_engine,
    tmp_path,
) -> None:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    items = tuple(
        SourceLoadItem(
            source_table="accounts",
            source_primary_key=f"account-{number}",
            canonical_source_key=f"{number:04d}",
            source_hash=hashlib.sha256(f"account-{number}".encode()).digest(),
            book_id=scenario.book_id,
            target_entity_id=None,
            payload={},
        )
        for number in (1, 2)
    )
    ResumableBackfillLoader(
        factory,
        snapshot_id="sha256:snapshot-seal",
        manifest_hash=b"s" * 32,
        apply_item=lambda _session, _item: None,
    ).load(items)

    evidence = seal_backfill(
        factory,
        snapshot_id="sha256:snapshot-seal",
        manifest_hash=b"s" * 32,
        source_counts={"accounts": 2},
        terminal_book_hashes={str(scenario.book_id): "ab" * 32},
    )
    replay = seal_backfill(
        factory,
        snapshot_id="sha256:snapshot-seal",
        manifest_hash=b"s" * 32,
        source_counts={"accounts": 2},
        terminal_book_hashes={str(scenario.book_id): "ab" * 32},
    )
    assert replay == evidence
    assert (evidence.receipt_count, evidence.quarantine_count) == (2, 0)

    first = tmp_path / "a" / "verification.json"
    second = tmp_path / "b" / "verification.json"
    write_verification(first, evidence)
    write_verification(second, replay)
    assert first.read_bytes() == second.read_bytes()

    with pytest.raises(BackfillChangedSourceError, match="different evidence"):
        seal_backfill(
            factory,
            snapshot_id="sha256:snapshot-seal",
            manifest_hash=b"z" * 32,
            source_counts={"accounts": 2},
            terminal_book_hashes={str(scenario.book_id): "ab" * 32},
        )
