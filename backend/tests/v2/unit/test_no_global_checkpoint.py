from __future__ import annotations

import inspect

from track_anywhere.infrastructure.db.models.async_projection import (
    ProjectionCheckpointRecord,
)
from track_anywhere.infrastructure.projections import event_reader
from track_anywhere.infrastructure.projections.event_reader import PerBookEventReader


def test_projection_checkpoint_schema_is_strictly_per_book() -> None:
    columns = set(ProjectionCheckpointRecord.__table__.columns.keys())
    assert {"book_id", "last_book_position"} <= columns
    assert "last_global_position" not in columns
    assert "global_sequence" not in columns


def test_runtime_event_reader_has_no_global_completion_or_ordering_logic() -> None:
    source = inspect.getsource(event_reader)
    lowered = source.lower()
    assert "last_global_position" not in lowered
    assert "global_sequence" not in lowered
    assert "order_by(ledgereventrecord.book_position)" in lowered.replace("\n", "")
    assert "ledgereventrecord.book_id == book_id" in lowered
    assert list(inspect.signature(PerBookEventReader.read_after).parameters) == [
        "self",
        "session",
        "book_id",
        "after_book_position",
        "limit",
    ]
