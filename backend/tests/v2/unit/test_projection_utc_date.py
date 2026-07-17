from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from track_anywhere.infrastructure.projections.dirty_periods import utc_date


def test_utc_date_is_independent_of_source_offset() -> None:
    instant = datetime(2026, 1, 31, 12, 30, tzinfo=UTC)

    assert utc_date(instant) == date(2026, 1, 31)
    assert utc_date(instant.astimezone(timezone(timedelta(hours=13)))) == date(
        2026, 1, 31
    )


@pytest.mark.parametrize("value", [datetime(2026, 1, 31), "2026-01-31"])
def test_utc_date_rejects_invalid_financial_instants(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="financial instant"):
        utc_date(value)  # type: ignore[arg-type]
