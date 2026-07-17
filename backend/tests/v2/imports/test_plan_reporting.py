from __future__ import annotations

from track_anywhere.domain.reporting.events import ReportingLinesAssigned

from backend.tests.v2.imports.test_plan_archive import approved_plan


def test_fixed_plan_compiles_final_current_reporting_only() -> None:
    plan = approved_plan()
    reporting = [
        event for event in plan.events if type(event.payload) is ReportingLinesAssigned
    ]

    assert len(reporting) == 38
    assert sum(len(event.payload.lines) for event in reporting) == 38
    assert all(event.payload.classification_revision == 1 for event in reporting)
    assert plan.archive.record_counts is not None
    assert plan.archive.record_counts.uncategorized_fx_reporting_facts == 5
