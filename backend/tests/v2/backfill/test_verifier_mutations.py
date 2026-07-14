from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from backend.tests.v2.backfill.corruption_harness import CorruptionHarness
from backend.tests.v2.backfill.test_independent_verifier import (
    VerifierScenario,
    seed_verifier_target,
)
from backend.tools.backfill_v1.verify import verify_target


def test_runtime_role_cannot_mutate_events_or_disable_integrity_trigger(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="protection", schema="v2")
    scenario = seed_verifier_target(database)
    engine = create_engine(database.runtime_url)
    try:
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "update ledger_events set previous_hash=:hash "
                        "where book_id=:book_id"
                    ),
                    {"hash": b"x" * 32, "book_id": scenario.monthly.journal.book_id},
                )
        with pytest.raises((DBAPIError, ProgrammingError)):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "alter table ledger_events disable trigger "
                    "trg_ledger_events_immutable"
                )
    finally:
        engine.dispose()


def test_corruption_harness_rejects_runtime_admin_and_foreign_factory_databases(
    postgres_database_factory,
) -> None:
    database = postgres_database_factory.create(purpose="owned", schema="v2")
    with pytest.raises(ValueError, match="migrator"):
        CorruptionHarness(
            postgres_database_factory,
            database,
            connection_url=database.runtime_url,
        )
    with pytest.raises(ValueError, match="migrator"):
        CorruptionHarness(
            postgres_database_factory,
            database,
            connection_url=database.admin_url,
        )

    class ForeignFactory:
        test_uuid = "not_this_factory"
        _created: dict[str, object] = {}

    with pytest.raises(ValueError, match="current test factory"):
        CorruptionHarness(ForeignFactory(), database)


Mutation = Callable[[CorruptionHarness, VerifierScenario], None]


def _lost_posting(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.delete_posting(
        book_id=scenario.monthly.journal.book_id,
        transaction_id=scenario.original_transaction_id,
    )


def _swapped_side(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.swap_posting_side(
        book_id=scenario.monthly.journal.book_id,
        transaction_id=scenario.original_transaction_id,
    )


def _wrong_time(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.shift_transaction_effective_time(
        book_id=scenario.monthly.journal.book_id,
        transaction_id=scenario.original_transaction_id,
    )


def _changed_classification(
    harness: CorruptionHarness, scenario: VerifierScenario
) -> None:
    harness.replace_reporting_dimension(
        book_id=scenario.monthly.journal.book_id,
        transaction_id=scenario.original_transaction_id,
        dimension_id=scenario.other_book_category_id,
    )


def _duplicate_reversal(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.duplicate_reversal_event(
        book_id=scenario.monthly.journal.book_id,
        original_transaction_id=scenario.original_transaction_id,
    )


def _broken_hash(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.break_previous_hash(book_id=scenario.monthly.journal.book_id)


def _noncontiguous(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.append_noncontiguous_event(book_id=scenario.monthly.journal.book_id)


def _cross_book(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.replace_reporting_dimension(
        book_id=scenario.monthly.journal.book_id,
        transaction_id=scenario.original_transaction_id,
        dimension_id=scenario.other_book_category_id,
    )


def _usdt_units(harness: CorruptionHarness, scenario: VerifierScenario) -> None:
    harness.increment_posting_units(
        book_id=scenario.monthly.journal.book_id,
        posting_id=scenario.usdt_debit_posting_id,
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        pytest.param(_lost_posting, "lost_posting", id="lost-posting"),
        pytest.param(_swapped_side, "posting_side_mismatch", id="swapped-side"),
        pytest.param(_wrong_time, "effective_time_mismatch", id="wrong-time"),
        pytest.param(
            _changed_classification,
            "classification_mismatch",
            id="changed-classification",
        ),
        pytest.param(
            _duplicate_reversal,
            "duplicate_reversal",
            id="duplicate-reversal",
        ),
        pytest.param(_broken_hash, "previous_hash_mismatch", id="broken-hash"),
        pytest.param(
            _noncontiguous,
            "book_position_noncontiguous",
            id="noncontiguous-position-version",
        ),
        pytest.param(_cross_book, "cross_book_link", id="cross-book-link"),
        pytest.param(_usdt_units, "usdt_unit_mismatch", id="modified-usdt-unit"),
    ],
)
def test_independent_verifier_detects_each_owner_only_mutation(
    postgres_database_factory,
    mutation: Mutation,
    expected_code: str,
) -> None:
    database = postgres_database_factory.create(purpose="mutation", schema="v2")
    scenario = seed_verifier_target(database)
    harness = CorruptionHarness(postgres_database_factory, database)

    mutation(harness, scenario)
    report = verify_target(database.runtime_url)

    assert report.status == "FAIL"
    assert expected_code in report.issue_codes
