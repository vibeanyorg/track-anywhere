from __future__ import annotations

from track_anywhere.application.imports.contracts import plan_summary

from backend.tests.v2.imports.test_plan_archive import approved_plan


def test_fixed_plan_repr_and_summary_do_not_expose_protected_payloads() -> None:
    plan = approved_plan()
    rendered = repr(plan)
    summary = plan_summary(plan)

    for content in (*plan.descriptions, plan.archive):
        plaintext = content.canonical_plaintext.decode("utf-8")
        assert plaintext not in rendered
        assert plaintext not in str(summary)
    assert set(summary) == {
        "contract_version",
        "source_dump_hash",
        "manifest_hash",
        "card_review_hash",
        "plan_hash",
        "expected_terminal_hash",
        "counts",
    }
