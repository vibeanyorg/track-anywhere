from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.tests.v2.postgres.test_deferred_balance_trigger import (
    _insert_asset,
    _insert_balanced_pair,
    _insert_catalog,
    _insert_event_and_transaction,
    _insert_posting,
)


PROJECTION_TABLES = {
    "account_balances",
    "journal_postings",
    "journal_transactions",
    "reporting_lines",
    "synchronous_projection_applied_events",
    "synchronous_projection_event_types",
    "transaction_external_references",
    "transaction_reversals",
}


def _insert_raw_event(
    connection,
    *,
    book_id: UUID,
    book_position: int,
    event_type: str,
    event_schema_version: int = 1,
    event_id: UUID | None = None,
    stream_id: UUID | None = None,
) -> UUID:
    persisted_event_id = event_id or uuid4()
    connection.execute(
        text(
            """
            insert into ledger_events (
                event_id, book_id, book_position, stream_type, stream_id,
                stream_version, event_type, event_schema_version, command_id,
                actor_subject_id, correlation_id, causation_event_id,
                effective_at, payload, previous_hash, event_hash
            ) values (
                :event_id, :book_id, :book_position, :stream_type, :stream_id,
                1, :event_type, :event_schema_version, :command_id,
                'human:test-user',
                :correlation_id, null, :effective_at, '{}'::jsonb,
                :previous_hash, :event_hash
            )
            """
        ),
        {
            "event_id": persisted_event_id,
            "book_id": book_id,
            "book_position": book_position,
            "stream_type": (
                "investment_lot"
                if event_type.startswith("InvestmentLot")
                else "journal_transaction"
            ),
            "stream_id": stream_id or uuid4(),
            "event_type": event_type,
            "event_schema_version": event_schema_version,
            "command_id": uuid4(),
            "correlation_id": uuid4(),
            "effective_at": datetime.now(UTC),
            "previous_hash": bytes(32),
            "event_hash": persisted_event_id.bytes * 2,
        },
    )
    return persisted_event_id


def _insert_projected_transaction(
    connection,
    *,
    book_id: UUID,
    transaction_id: UUID,
    event_id: UUID,
    book_position: int,
    debit_account_id: UUID,
    credit_account_id: UUID,
) -> None:
    _insert_event_and_transaction(
        connection,
        book_id=book_id,
        transaction_id=transaction_id,
        event_id=event_id,
        book_position=book_position,
    )
    _insert_balanced_pair(
        connection,
        book_id=book_id,
        transaction_id=transaction_id,
        debit_account_id=debit_account_id,
        credit_account_id=credit_account_id,
    )


def _insert_reporting_line(
    connection,
    *,
    book_id: UUID,
    transaction_id: UUID,
    source_event_id: UUID,
    classification_revision: int,
    line_position: int,
) -> None:
    connection.execute(
        text(
            """
            insert into reporting_lines (
                book_id, transaction_id, classification_revision,
                line_id, line_version_id, catalog_id, line_position,
                asset_code, units, line_kind, dimension, dimension_id,
                description_ref, source_event_id
            ) values (
                :book_id, :transaction_id, :classification_revision,
                :line_id, :line_version_id, :catalog_id, :line_position,
                'USD', 100, 'expense', 'category', null, null,
                :source_event_id
            )
            """
        ),
        {
            "book_id": book_id,
            "transaction_id": transaction_id,
            "classification_revision": classification_revision,
            "line_id": uuid4(),
            "line_version_id": uuid4(),
            "catalog_id": uuid4(),
            "line_position": line_position,
            "source_event_id": source_event_id,
        },
    )


def test_projection_relations_native_type_and_model_metadata_are_complete(
    pg_engine,
) -> None:
    from track_anywhere.infrastructure.db.base import V2Base, load_v2_models

    load_v2_models()
    assert PROJECTION_TABLES.issubset(V2Base.metadata.tables)

    with pg_engine.connect() as connection:
        relations = {
            table_name: connection.execute(
                text("select to_regclass(:relation_name)"),
                {"relation_name": f"public.{table_name}"},
            ).scalar_one()
            for table_name in PROJECTION_TABLES
        }
        posting_side = connection.execute(
            text(
                """
                select type_record.typtype,
                       array_agg(enum_record.enumlabel order by enum_record.enumsortorder)
                  from pg_catalog.pg_type type_record
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = type_record.typnamespace
                  join pg_catalog.pg_enum enum_record
                    on enum_record.enumtypid = type_record.oid
                 where namespace.nspname = 'public'
                   and type_record.typname = 'posting_side'
                 group by type_record.typtype
                """
            )
        ).one()
        sync_event_types = connection.execute(
            text(
                """
                select event_type, event_schema_version, projection_version
                  from synchronous_projection_event_types
                 order by event_type, event_schema_version
                """
            )
        ).all()

    assert all(relations.values())
    assert tuple(posting_side) == ("e", ["debit", "credit"])
    assert [tuple(row) for row in sync_event_types] == [
        ("FinancialExternalReferenceCorrected", 1, 1),
        ("InvestmentLotAcquired", 1, 1),
        ("InvestmentLotDisposed", 1, 1),
        ("JournalTransactionPosted", 1, 1),
        ("JournalTransactionReversed", 1, 1),
        ("ReportingLinesAssigned", 1, 1),
        ("ReportingLinesCleared", 1, 1),
    ]


def test_postings_enforce_account_asset_book_and_unique_ordering(pg_engine) -> None:
    first_book, first_debit, first_credit = _insert_catalog(pg_engine)
    second_book, second_account, _ = _insert_catalog(pg_engine)
    _insert_asset(pg_engine, "EUR")

    transaction_id = uuid4()
    first_posting_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_event_and_transaction(
            connection,
            book_id=first_book,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
        )
        _insert_posting(
            connection,
            book_id=first_book,
            transaction_id=transaction_id,
            account_id=first_debit,
            posting_position=0,
            posting_id=first_posting_id,
            side="debit",
        )
        _insert_posting(
            connection,
            book_id=first_book,
            transaction_id=transaction_id,
            account_id=first_credit,
            posting_position=1,
            side="credit",
        )

    invalid_postings = (
        {
            "account_id": second_account,
            "asset_code": "USD",
            "posting_position": 2,
            "posting_id": uuid4(),
        },
        {
            "account_id": first_debit,
            "asset_code": "EUR",
            "posting_position": 2,
            "posting_id": uuid4(),
        },
        {
            "account_id": first_debit,
            "asset_code": "USD",
            "posting_position": 0,
            "posting_id": uuid4(),
        },
        {
            "account_id": first_debit,
            "asset_code": "USD",
            "posting_position": 2,
            "posting_id": first_posting_id,
        },
    )
    for posting in invalid_postings:
        with pytest.raises(IntegrityError):
            with pg_engine.begin() as connection:
                _insert_posting(
                    connection,
                    book_id=first_book,
                    transaction_id=transaction_id,
                    account_id=posting["account_id"],
                    asset_code=posting["asset_code"],
                    posting_position=posting["posting_position"],
                    posting_id=posting["posting_id"],
                    side="debit",
                )

    assert first_book != second_book


def test_posting_numeric_38_digit_boundary_is_exact_and_39_digits_fail(
    pg_engine,
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    maximum_units = "9" * 38
    transaction_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
        )
        _insert_balanced_pair(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            debit_account_id=debit_account_id,
            credit_account_id=credit_account_id,
            units=maximum_units,
        )

    with pg_engine.connect() as connection:
        stored = (
            connection.execute(
                text(
                    """
                select units
                  from journal_postings
                 where book_id = :book_id and transaction_id = :transaction_id
                 order by posting_position
                """
                ),
                {"book_id": book_id, "transaction_id": transaction_id},
            )
            .scalars()
            .all()
        )
    assert stored == [Decimal(maximum_units), Decimal(maximum_units)]

    with pytest.raises(DBAPIError):
        with pg_engine.begin() as connection:
            overflow_transaction_id = uuid4()
            _insert_event_and_transaction(
                connection,
                book_id=book_id,
                transaction_id=overflow_transaction_id,
                event_id=uuid4(),
                book_position=2,
            )
            _insert_posting(
                connection,
                book_id=book_id,
                transaction_id=overflow_transaction_id,
                account_id=debit_account_id,
                posting_position=0,
                side="debit",
                units="1" + "0" * 38,
            )


@pytest.mark.parametrize(
    "event_type",
    (
        "JournalTransactionPosted",
        "InvestmentLotAcquired",
        "InvestmentLotDisposed",
    ),
)
def test_sync_required_events_need_a_marker(pg_engine, event_type: str) -> None:
    sync_book, _, _ = _insert_catalog(pg_engine)
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        _insert_raw_event(
            connection,
            book_id=sync_book,
            book_position=1,
            event_type=event_type,
        )
        with pytest.raises(DBAPIError):
            transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_known_sync_event_with_unknown_schema_is_rejected_at_commit_and_rolled_back(
    pg_engine,
) -> None:
    book_id, _, _ = _insert_catalog(pg_engine)
    event_id = uuid4()
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        _insert_raw_event(
            connection,
            book_id=book_id,
            book_position=1,
            event_type="JournalTransactionPosted",
            event_schema_version=2,
            event_id=event_id,
        )
        with pytest.raises(IntegrityError) as error:
            transaction.commit()
        assert error.value.orig.sqlstate == "23514"
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()

    with pg_engine.connect() as verification_connection:
        assert (
            verification_connection.execute(
                text("select count(*) from ledger_events where event_id = :event_id"),
                {"event_id": event_id},
            ).scalar_one()
            == 0
        )


def test_journal_transaction_source_requires_registered_type_and_schema(
    pg_engine,
) -> None:
    book_id, _, _ = _insert_catalog(pg_engine)
    transaction_id = uuid4()
    event_id = uuid4()
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        _insert_raw_event(
            connection,
            book_id=book_id,
            book_position=1,
            event_type="JournalTransactionPosted",
            event_schema_version=2,
            event_id=event_id,
            stream_id=transaction_id,
        )
        with pytest.raises(IntegrityError) as error:
            connection.execute(
                text(
                    """
                    insert into journal_transactions (
                        book_id, transaction_id, source_event_id,
                        source_position, effective_at, transaction_kind,
                        description_ref
                    ) values (
                        :book_id, :transaction_id, :event_id, 1,
                        :effective_at, 'standard', null
                    )
                    """
                ),
                {
                    "book_id": book_id,
                    "transaction_id": transaction_id,
                    "event_id": event_id,
                    "effective_at": datetime.now(UTC),
                },
            )
        assert error.value.orig.sqlstate == "23514"
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_marker_accepts_only_registered_sync_events_and_is_database_timestamped(
    pg_engine,
) -> None:
    sync_book, _, _ = _insert_catalog(pg_engine)
    event_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_raw_event(
            connection,
            book_id=sync_book,
            book_position=1,
            event_type="JournalTransactionPosted",
            event_id=event_id,
        )
        connection.execute(
            text(
                """
                insert into synchronous_projection_applied_events (
                    book_id, event_id, projection_version
                ) values (:book_id, :event_id, 1)
                """
            ),
            {"book_id": sync_book, "event_id": event_id},
        )

    with pg_engine.connect() as connection:
        applied_at = connection.execute(
            text(
                """
                select applied_at
                  from synchronous_projection_applied_events
                 where book_id = :book_id and event_id = :event_id
                """
            ),
            {"book_id": sync_book, "event_id": event_id},
        ).scalar_one()
    assert applied_at is not None

    unregistered_book, _, _ = _insert_catalog(pg_engine)
    with pytest.raises(DBAPIError):
        with pg_engine.begin() as connection:
            unregistered_event_id = _insert_raw_event(
                connection,
                book_id=unregistered_book,
                book_position=1,
                event_type="AsyncProjectionRequested",
            )
            connection.execute(
                text(
                    """
                    insert into synchronous_projection_applied_events (
                        book_id, event_id, projection_version
                    ) values (:book_id, :event_id, 1)
                    """
                ),
                {
                    "book_id": unregistered_book,
                    "event_id": unregistered_event_id,
                },
            )

    invalid_version_book, _, _ = _insert_catalog(pg_engine)
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            invalid_version_event_id = _insert_raw_event(
                connection,
                book_id=invalid_version_book,
                book_position=1,
                event_type="JournalTransactionPosted",
            )
            connection.execute(
                text(
                    """
                    insert into synchronous_projection_applied_events (
                        book_id, event_id, projection_version
                    ) values (:book_id, :event_id, 0)
                    """
                ),
                {
                    "book_id": invalid_version_book,
                    "event_id": invalid_version_event_id,
                },
            )


def test_marker_projection_version_must_match_the_registry(pg_engine) -> None:
    book_id, _, _ = _insert_catalog(pg_engine)
    with pytest.raises(IntegrityError) as error:
        with pg_engine.begin() as connection:
            event_id = _insert_raw_event(
                connection,
                book_id=book_id,
                book_position=1,
                event_type="JournalTransactionPosted",
            )
            connection.execute(
                text(
                    """
                    insert into synchronous_projection_applied_events (
                        book_id, event_id, projection_version
                    ) values (:book_id, :event_id, 2147483647)
                    """
                ),
                {"book_id": book_id, "event_id": event_id},
            )
    assert error.value.orig.sqlstate == "23514"


def test_runtime_can_replace_all_and_clear_only_current_reporting_lines(
    pg_engine,
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    transaction_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_projected_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=uuid4(),
            book_position=1,
            debit_account_id=debit_account_id,
            credit_account_id=credit_account_id,
        )
        assigned_event_id = _insert_raw_event(
            connection,
            book_id=book_id,
            book_position=2,
            event_type="ReportingLinesAssigned",
        )
        connection.execute(
            text(
                """
                insert into synchronous_projection_applied_events (
                    book_id, event_id, projection_version
                ) values (:book_id, :event_id, 1)
                """
            ),
            {"book_id": book_id, "event_id": assigned_event_id},
        )
        for line_position in (0, 1):
            _insert_reporting_line(
                connection,
                book_id=book_id,
                transaction_id=transaction_id,
                source_event_id=assigned_event_id,
                classification_revision=1,
                line_position=line_position,
            )

    with pg_engine.begin() as connection:
        replacement_event_id = _insert_raw_event(
            connection,
            book_id=book_id,
            book_position=3,
            event_type="ReportingLinesAssigned",
        )
        connection.execute(
            text(
                """
                insert into synchronous_projection_applied_events (
                    book_id, event_id, projection_version
                ) values (:book_id, :event_id, 1)
                """
            ),
            {"book_id": book_id, "event_id": replacement_event_id},
        )
        connection.execute(
            text(
                """
                delete from reporting_lines
                 where book_id = :book_id and transaction_id = :transaction_id
                """
            ),
            {"book_id": book_id, "transaction_id": transaction_id},
        )
        _insert_reporting_line(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            source_event_id=replacement_event_id,
            classification_revision=2,
            line_position=0,
        )

    with pg_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                select classification_revision, line_position, source_event_id
                  from reporting_lines
                 where book_id = :book_id and transaction_id = :transaction_id
                """
            ),
            {"book_id": book_id, "transaction_id": transaction_id},
        ).all()
    assert [tuple(row) for row in rows] == [(2, 0, replacement_event_id)]

    with pg_engine.begin() as connection:
        cleared_event_id = _insert_raw_event(
            connection,
            book_id=book_id,
            book_position=4,
            event_type="ReportingLinesCleared",
        )
        connection.execute(
            text(
                """
                insert into synchronous_projection_applied_events (
                    book_id, event_id, projection_version
                ) values (:book_id, :event_id, 1)
                """
            ),
            {"book_id": book_id, "event_id": cleared_event_id},
        )
        connection.execute(
            text(
                """
                delete from reporting_lines
                 where book_id = :book_id and transaction_id = :transaction_id
                """
            ),
            {"book_id": book_id, "transaction_id": transaction_id},
        )

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                  from reporting_lines
                 where book_id = :book_id and transaction_id = :transaction_id
                """
                ),
                {"book_id": book_id, "transaction_id": transaction_id},
            ).scalar_one()
            == 0
        )


def test_balance_numeric_48_digit_positive_and_negative_boundaries_are_exact(
    pg_engine,
) -> None:
    book_id, positive_account_id, negative_account_id = _insert_catalog(pg_engine)
    transaction_id = uuid4()
    event_id = uuid4()
    maximum = "9" * 48
    minimum = "-" + maximum
    with pg_engine.begin() as connection:
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=event_id,
            book_position=1,
        )
        _insert_balanced_pair(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            debit_account_id=positive_account_id,
            credit_account_id=negative_account_id,
        )
        connection.execute(
            text(
                """
                insert into account_balances (
                    book_id, account_id, asset_code, balance_units, as_of_position
                ) values
                    (:book_id, :positive_account_id, 'USD',
                     cast(:maximum as numeric), 1),
                    (:book_id, :negative_account_id, 'USD',
                     cast(:minimum as numeric), 1)
                """
            ),
            {
                "book_id": book_id,
                "positive_account_id": positive_account_id,
                "negative_account_id": negative_account_id,
                "maximum": maximum,
                "minimum": minimum,
            },
        )

    with pg_engine.connect() as connection:
        balances = (
            connection.execute(
                text(
                    """
                select balance_units
                  from account_balances
                 where book_id = :book_id
                 order by balance_units desc
                """
                ),
                {"book_id": book_id},
            )
            .scalars()
            .all()
        )
    assert balances == [Decimal(maximum), Decimal(minimum)]


def test_49_digit_balance_overflow_rolls_back_event_marker_and_projections(
    pg_engine,
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    transaction_id = uuid4()
    event_id = uuid4()
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        _insert_event_and_transaction(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            event_id=event_id,
            book_position=1,
        )
        _insert_balanced_pair(
            connection,
            book_id=book_id,
            transaction_id=transaction_id,
            debit_account_id=debit_account_id,
            credit_account_id=credit_account_id,
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    """
                    insert into account_balances (
                        book_id, account_id, asset_code,
                        balance_units, as_of_position
                    ) values (
                        :book_id, :account_id, 'USD',
                        cast(:overflow as numeric), 1
                    )
                    """
                ),
                {
                    "book_id": book_id,
                    "account_id": debit_account_id,
                    "overflow": "1" + "0" * 48,
                },
            )
        transaction.rollback()
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()

    with pg_engine.connect() as verification_connection:
        counts = verification_connection.execute(
            text(
                """
                select
                    (select count(*) from ledger_events
                      where event_id = :event_id) as event_count,
                    (select count(*) from synchronous_projection_applied_events
                      where event_id = :event_id) as marker_count,
                    (select count(*) from journal_transactions
                      where transaction_id = :transaction_id) as transaction_count,
                    (select count(*) from journal_postings
                      where transaction_id = :transaction_id) as posting_count,
                    (select count(*) from account_balances
                      where book_id = :book_id) as balance_count
                """
            ),
            {
                "event_id": event_id,
                "transaction_id": transaction_id,
                "book_id": book_id,
            },
        ).one()
    assert tuple(counts) == (0, 0, 0, 0, 0)


def test_reversal_target_is_unique_and_cannot_cross_books(pg_engine) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    transaction_ids = [uuid4() for _ in range(3)]
    event_ids = [uuid4() for _ in range(3)]
    with pg_engine.begin() as connection:
        for position, (transaction_id, event_id) in enumerate(
            zip(transaction_ids, event_ids, strict=True), start=1
        ):
            _insert_projected_transaction(
                connection,
                book_id=book_id,
                transaction_id=transaction_id,
                event_id=event_id,
                book_position=position,
                debit_account_id=debit_account_id,
                credit_account_id=credit_account_id,
            )

    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into transaction_reversals (
                    book_id, reversal_transaction_id, original_transaction_id,
                    source_event_id, original_event_id, original_event_hash,
                    reason_code
                ) values (
                    :book_id, :reversal_transaction_id, :original_transaction_id,
                    :source_event_id, :original_event_id, :original_event_hash,
                    'user_correction'
                )
                """
            ),
            {
                "book_id": book_id,
                "reversal_transaction_id": transaction_ids[1],
                "original_transaction_id": transaction_ids[0],
                "source_event_id": event_ids[1],
                "original_event_id": event_ids[0],
                "original_event_hash": event_ids[0].bytes * 2,
            },
        )

    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into transaction_reversals (
                        book_id, reversal_transaction_id, original_transaction_id,
                        source_event_id, original_event_id, original_event_hash,
                        reason_code
                    ) values (
                        :book_id, :reversal_transaction_id,
                        :original_transaction_id, :source_event_id,
                        :original_event_id, :original_event_hash, 'duplicate'
                    )
                    """
                ),
                {
                    "book_id": book_id,
                    "reversal_transaction_id": transaction_ids[2],
                    "original_transaction_id": transaction_ids[0],
                    "source_event_id": event_ids[2],
                    "original_event_id": event_ids[0],
                    "original_event_hash": event_ids[0].bytes * 2,
                },
            )

    other_book, other_debit, other_credit = _insert_catalog(pg_engine)
    other_transaction_id = uuid4()
    other_event_id = uuid4()
    with pg_engine.begin() as connection:
        _insert_projected_transaction(
            connection,
            book_id=other_book,
            transaction_id=other_transaction_id,
            event_id=other_event_id,
            book_position=1,
            debit_account_id=other_debit,
            credit_account_id=other_credit,
        )

    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into transaction_reversals (
                        book_id, reversal_transaction_id, original_transaction_id,
                        source_event_id, original_event_id, original_event_hash,
                        reason_code
                    ) values (
                        :book_id, :reversal_transaction_id,
                        :original_transaction_id, :source_event_id,
                        :original_event_id, :original_event_hash, 'duplicate'
                    )
                    """
                ),
                {
                    "book_id": book_id,
                    "reversal_transaction_id": transaction_ids[2],
                    "original_transaction_id": other_transaction_id,
                    "source_event_id": event_ids[2],
                    "original_event_id": other_event_id,
                    "original_event_hash": other_event_id.bytes * 2,
                },
            )


def test_projection_and_causation_foreign_keys_are_book_composite(pg_engine) -> None:
    with pg_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                select source.relname as source_table,
                       target.relname as target_table,
                       array(
                           select attribute.attname
                             from unnest(constraint_record.conkey)
                                  with ordinality key_column(attnum, position)
                             join pg_catalog.pg_attribute attribute
                               on attribute.attrelid = constraint_record.conrelid
                              and attribute.attnum = key_column.attnum
                            order by key_column.position
                       ) as source_columns,
                       array(
                           select attribute.attname
                             from unnest(constraint_record.confkey)
                                  with ordinality key_column(attnum, position)
                             join pg_catalog.pg_attribute attribute
                               on attribute.attrelid = constraint_record.confrelid
                              and attribute.attnum = key_column.attnum
                            order by key_column.position
                       ) as target_columns
                  from pg_catalog.pg_constraint constraint_record
                  join pg_catalog.pg_class source
                    on source.oid = constraint_record.conrelid
                  join pg_catalog.pg_class target
                    on target.oid = constraint_record.confrelid
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = source.relnamespace
                 where namespace.nspname = 'public'
                   and constraint_record.contype = 'f'
                   and source.relname in (
                       'ledger_events', 'journal_transactions',
                       'journal_postings', 'account_balances',
                       'transaction_reversals',
                       'transaction_external_references', 'reporting_lines',
                       'synchronous_projection_applied_events'
                   )
                """
            )
        ).all()

    actual = {
        (
            row.source_table,
            row.target_table,
            tuple(row.source_columns),
            tuple(row.target_columns),
        )
        for row in rows
    }
    expected = {
        (
            "ledger_events",
            "ledger_events",
            ("book_id", "causation_event_id"),
            ("book_id", "event_id"),
        ),
        (
            "journal_transactions",
            "ledger_events",
            ("book_id", "source_event_id"),
            ("book_id", "event_id"),
        ),
        (
            "journal_transactions",
            "protected_description_sidecars",
            ("book_id", "description_ref"),
            ("book_id", "sidecar_id"),
        ),
        (
            "journal_postings",
            "accounts",
            ("book_id", "account_id", "asset_code"),
            ("book_id", "account_id", "asset_code"),
        ),
        (
            "journal_postings",
            "journal_transactions",
            ("book_id", "transaction_id"),
            ("book_id", "transaction_id"),
        ),
        (
            "account_balances",
            "accounts",
            ("book_id", "account_id", "asset_code"),
            ("book_id", "account_id", "asset_code"),
        ),
        (
            "account_balances",
            "ledger_events",
            ("book_id", "as_of_position"),
            ("book_id", "book_position"),
        ),
        (
            "transaction_reversals",
            "journal_transactions",
            ("book_id", "reversal_transaction_id", "source_event_id"),
            ("book_id", "transaction_id", "source_event_id"),
        ),
        (
            "transaction_reversals",
            "journal_transactions",
            ("book_id", "original_transaction_id", "original_event_id"),
            ("book_id", "transaction_id", "source_event_id"),
        ),
        (
            "transaction_external_references",
            "journal_transactions",
            ("book_id", "transaction_id"),
            ("book_id", "transaction_id"),
        ),
        (
            "transaction_external_references",
            "ledger_events",
            ("book_id", "source_event_id"),
            ("book_id", "event_id"),
        ),
        (
            "reporting_lines",
            "journal_transactions",
            ("book_id", "transaction_id"),
            ("book_id", "transaction_id"),
        ),
        (
            "reporting_lines",
            "ledger_events",
            ("book_id", "source_event_id"),
            ("book_id", "event_id"),
        ),
        (
            "reporting_lines",
            "protected_description_sidecars",
            ("book_id", "description_ref"),
            ("book_id", "sidecar_id"),
        ),
        (
            "synchronous_projection_applied_events",
            "ledger_events",
            ("book_id", "event_id"),
            ("book_id", "event_id"),
        ),
    }
    assert expected.issubset(actual)


def test_referenced_account_and_asset_accounting_identity_cannot_change_concurrently(
    pg_engine,
) -> None:
    book_id, debit_account_id, credit_account_id = _insert_catalog(pg_engine)
    _insert_asset(pg_engine, "EUR")
    writer = pg_engine.connect()
    writer_transaction = writer.begin()
    try:
        _insert_projected_transaction(
            writer,
            book_id=book_id,
            transaction_id=uuid4(),
            event_id=uuid4(),
            book_position=1,
            debit_account_id=debit_account_id,
            credit_account_id=credit_account_id,
        )

        mutations = (
            (
                "update assets set ledger_scale = 3 where asset_code = 'USD'",
                {},
            ),
            (
                """
                update accounts
                   set asset_code = 'EUR'
                 where book_id = :book_id and account_id = :account_id
                """,
                {"book_id": book_id, "account_id": debit_account_id},
            ),
            (
                """
                update accounts
                   set system_role = 'fx_trading'
                 where book_id = :book_id and account_id = :account_id
                """,
                {"book_id": book_id, "account_id": debit_account_id},
            ),
        )
        for statement, parameters in mutations:
            mutator = pg_engine.connect()
            mutator_transaction = mutator.begin()
            try:
                mutator.execute(text("set local lock_timeout = '150ms'"))
                with pytest.raises(DBAPIError):
                    mutator.execute(text(statement), parameters)
            finally:
                if mutator_transaction.is_active:
                    mutator_transaction.rollback()
                mutator.close()
    finally:
        if writer_transaction.is_active:
            writer_transaction.rollback()
        writer.close()


def test_balance_and_sync_guards_are_deferred_constraint_triggers(pg_engine) -> None:
    with pg_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    """
                select relation.relname as table_name,
                       trigger_record.tgname as trigger_name,
                       trigger_record.tgdeferrable,
                       trigger_record.tginitdeferred,
                       pg_catalog.pg_get_triggerdef(trigger_record.oid) as definition
                  from pg_catalog.pg_trigger trigger_record
                  join pg_catalog.pg_class relation
                    on relation.oid = trigger_record.tgrelid
                 where not trigger_record.tgisinternal
                   and trigger_record.tgname in (
                       'trg_journal_transactions_balanced_commit',
                       'trg_journal_postings_balanced_commit',
                       'trg_ledger_events_sync_projection_commit'
                   )
                 order by trigger_record.tgname
                """
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 3
    by_name = {row["trigger_name"]: row for row in rows}
    assert by_name["trg_journal_transactions_balanced_commit"]["table_name"] == (
        "journal_transactions"
    )
    assert "INSERT" in by_name["trg_journal_transactions_balanced_commit"]["definition"]
    posting_definition = by_name["trg_journal_postings_balanced_commit"]["definition"]
    assert by_name["trg_journal_postings_balanced_commit"]["table_name"] == (
        "journal_postings"
    )
    assert all(
        operation in posting_definition for operation in ("INSERT", "UPDATE", "DELETE")
    )
    sync_definition = by_name["trg_ledger_events_sync_projection_commit"]["definition"]
    assert by_name["trg_ledger_events_sync_projection_commit"]["table_name"] == (
        "ledger_events"
    )
    assert "INSERT" in sync_definition
    for row in rows:
        assert row["tgdeferrable"] is True
        assert row["tginitdeferred"] is True


def _column_privileges(connection, role: str, table_name: str, privilege: str):
    return {
        column_name
        for column_name in connection.execute(
            text(
                """
                select attribute.attname
                  from pg_catalog.pg_attribute attribute
                  join pg_catalog.pg_class relation
                    on relation.oid = attribute.attrelid
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = relation.relnamespace
                 where namespace.nspname = 'public'
                   and relation.relname = :table_name
                   and attribute.attnum > 0
                   and not attribute.attisdropped
                   and has_column_privilege(
                       :role,
                       format('%I.%I', namespace.nspname, relation.relname),
                       attribute.attname,
                       :privilege
                   )
                """
            ),
            {
                "role": role,
                "table_name": table_name,
                "privilege": privilege,
            },
        ).scalars()
    }


def test_runtime_projection_acl_and_trigger_functions_are_minimal(
    pg_engine,
    migrated_postgres_database,
) -> None:
    runtime = migrated_postgres_database.runtime_role
    with pg_engine.connect() as connection:
        for table_name in PROJECTION_TABLES:
            assert connection.execute(
                text("select has_table_privilege(:role, :table_name, 'SELECT')"),
                {"role": runtime, "table_name": f"public.{table_name}"},
            ).scalar_one()
            assert connection.execute(
                text("select has_table_privilege(:role, :table_name, 'DELETE')"),
                {"role": runtime, "table_name": f"public.{table_name}"},
            ).scalar_one() is (table_name == "reporting_lines")
            for privilege in (
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
                "MAINTAIN",
            ):
                assert not connection.execute(
                    text("select has_table_privilege(:role, :table_name, :privilege)"),
                    {
                        "role": runtime,
                        "table_name": f"public.{table_name}",
                        "privilege": privilege,
                    },
                ).scalar_one()

        assert _column_privileges(
            connection,
            runtime,
            "synchronous_projection_applied_events",
            "INSERT",
        ) == {"book_id", "event_id", "projection_version"}
        assert not _column_privileges(
            connection,
            runtime,
            "synchronous_projection_applied_events",
            "UPDATE",
        )
        assert not _column_privileges(
            connection,
            runtime,
            "synchronous_projection_event_types",
            "INSERT",
        )
        assert not _column_privileges(
            connection,
            runtime,
            "synchronous_projection_event_types",
            "UPDATE",
        )
        assert connection.execute(
            text("select has_type_privilege(:role, 'public.posting_side', 'USAGE')"),
            {"role": runtime},
        ).scalar_one()
        assert not connection.execute(
            text("select has_type_privilege('public', 'public.posting_side', 'USAGE')")
        ).scalar_one()

        functions = (
            connection.execute(
                text(
                    """
                select function_record.proname,
                       function_record.prosecdef,
                       function_record.proconfig,
                       exists(
                           select 1
                             from pg_catalog.aclexplode(
                                 coalesce(
                                     function_record.proacl,
                                     pg_catalog.acldefault(
                                         'f', function_record.proowner
                                     )
                                 )
                             ) acl
                             left join pg_catalog.pg_roles grantee
                               on grantee.oid = acl.grantee
                            where acl.privilege_type = 'EXECUTE'
                              and (acl.grantee = 0 or grantee.rolname = :runtime)
                       ) as broadly_executable
                  from pg_catalog.pg_proc function_record
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = function_record.pronamespace
                 where namespace.nspname = 'public'
                   and function_record.proname like 'v2_%projection%'
                       or namespace.nspname = 'public'
                      and function_record.proname = 'v2_validate_journal_balance'
                """
                ),
                {"runtime": runtime},
            )
            .mappings()
            .all()
        )

    assert functions
    for function in functions:
        assert function["prosecdef"] is False
        assert function["proconfig"] is not None
        assert any(
            setting.replace(" ", "") == "search_path=pg_catalog,public"
            for setting in function["proconfig"]
        )
        assert function["broadly_executable"] is False
