from __future__ import annotations

import hashlib

from track_anywhere.serialization.canonical_json import canonical_json_bytes
from track_anywhere.verification import _hash_rows


def test_projection_hash_consumes_a_one_shot_row_stream_incrementally() -> None:
    rows = [
        {"book_id": "book-1", "units": "100"},
        {"book_id": "book-2", "units": "-25"},
    ]
    yielded: list[int] = []

    def row_stream():
        for index, row in enumerate(rows):
            yielded.append(index)
            yield row

    expected = hashlib.sha256(canonical_json_bytes(rows)).hexdigest()

    assert _hash_rows(row_stream()) == expected
    assert yielded == [0, 1]
