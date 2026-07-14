from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

import pytest

from backend.tools.backfill_v1.load import BackfillSealBlocked, seal_backfill
from backend.tools.backfill_v1.quarantine import (
    decide_quarantine,
    record_quarantine,
)
from track_anywhere.infrastructure.db.models.backfill import BackfillQuarantineRecord


def test_any_quarantine_row_blocks_seal_and_sensitive_details_are_redacted(
    pg_engine,
) -> None:
    factory = sessionmaker(pg_engine, expire_on_commit=False)
    quarantine_id = record_quarantine(
        factory,
        snapshot_id="sha256:snapshot-q",
        source_table="transactions",
        source_primary_key="tx-1",
        reason_code="invalid_amount",
        details={"memo": "private merchant text", "field": "amount"},
    )
    with Session(pg_engine) as session:
        stored = session.get(BackfillQuarantineRecord, quarantine_id)
        assert stored is not None
        assert stored.details == {"memo": "[REDACTED]", "field": "amount"}

    for should_decide in (False, True):
        if should_decide:
            decide_quarantine(
                factory,
                quarantine_id,
                decision="skipped",
                actor_subject_id="operator:reviewed",
            )
        with pytest.raises(BackfillSealBlocked, match="quarantine"):
            seal_backfill(
                factory,
                snapshot_id="sha256:snapshot-q",
                manifest_hash=b"q" * 32,
                source_counts={},
                terminal_book_hashes={},
            )
