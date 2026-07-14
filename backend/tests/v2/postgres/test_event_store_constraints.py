from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError


EVENT_STORE_TABLES = (
    "book_event_heads",
    "command_receipts",
    "event_stream_heads",
    "ledger_events",
)


def _execute(engine: Engine, statement: str, parameters: dict[str, object]) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), parameters)


def _rejects_integrity(
    engine: Engine, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        _execute(engine, statement, parameters)


def _insert_book(engine: Engine, book_id: UUID, asset_code: str) -> None:
    _execute(
        engine,
        """
        insert into assets (
            asset_code, kind, ledger_scale, input_scale, display_scale,
            current_name, status
        ) values (
            :asset_code, 'fiat', 2, 2, 2, 'Test asset', 'active'
        ) on conflict (asset_code) do nothing
        """,
        {"asset_code": asset_code},
    )
    _execute(
        engine,
        """
        insert into books (book_id, current_name, base_asset_code, write_state)
        values (:book_id, 'Event book', :asset_code, 'active')
        """,
        {"book_id": book_id, "asset_code": asset_code},
    )


def _event_values(
    *,
    book_id: UUID,
    book_position: int,
    stream_id: UUID | None = None,
    stream_version: int = 1,
    event_id: UUID | None = None,
    causation_event_id: UUID | None = None,
    event_hash_byte: bytes = b"e",
) -> dict[str, object]:
    return {
        "event_id": event_id or uuid4(),
        "book_id": book_id,
        "book_position": book_position,
        "stream_type": "investment_lot",
        "stream_id": stream_id or uuid4(),
        "stream_version": stream_version,
        "event_type": "InvestmentLotAcquired",
        "event_schema_version": 1,
        "command_id": uuid4(),
        "actor_subject_id": "human:test-user",
        "correlation_id": uuid4(),
        "causation_event_id": causation_event_id,
        "effective_at": datetime.now(UTC),
        "payload": '{"transaction_id": "test"}',
        "previous_hash": b"p" * 32,
        "event_hash": event_hash_byte * 32,
    }


def _insert_event_on_connection(
    connection: Connection, values: dict[str, object]
) -> None:
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
                :stream_version, :event_type, :event_schema_version, :command_id,
                :actor_subject_id, :correlation_id, :causation_event_id,
                :effective_at, cast(:payload as jsonb), :previous_hash, :event_hash
            )
            """
        ),
        values,
    )
    connection.execute(
        text(
            """
            insert into synchronous_projection_applied_events (
                book_id, event_id, projection_version
            ) values (:book_id, :event_id, 1)
            """
        ),
        {"book_id": values["book_id"], "event_id": values["event_id"]},
    )


def _insert_event(engine: Engine, values: dict[str, object]) -> None:
    with engine.begin() as connection:
        _insert_event_on_connection(connection, values)


def test_event_store_relations_native_types_and_model_metadata_are_complete(
    pg_engine,
) -> None:
    from track_anywhere.infrastructure.db.base import V2Base, load_v2_models

    load_v2_models()
    assert set(EVENT_STORE_TABLES).issubset(V2Base.metadata.tables)

    with pg_engine.connect() as connection:
        relations = {
            name: connection.execute(
                text("select to_regclass(:name)"), {"name": f"public.{name}"}
            ).scalar_one()
            for name in (*EVENT_STORE_TABLES, "ledger_global_sequence")
        }
        receipt_type = connection.execute(
            text(
                """
                select type.typtype, array_agg(enum.enumlabel order by enum.enumsortorder)
                  from pg_catalog.pg_type type
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = type.typnamespace
                  join pg_catalog.pg_enum enum on enum.enumtypid = type.oid
                 where namespace.nspname = 'public'
                   and type.typname = 'receipt_status'
                 group by type.typtype
                """
            )
        ).one()

    assert all(relations.values())
    assert tuple(receipt_type) == ("e", ["processing", "completed"])


def test_events_enforce_book_position_stream_hash_payload_and_version_invariants(
    pg_engine,
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    stream_id = uuid4()
    first = _event_values(
        book_id=book_id,
        book_position=1,
        stream_id=stream_id,
        event_hash_byte=b"a",
    )
    _insert_event(pg_engine, first)

    duplicate_position = _event_values(
        book_id=book_id,
        book_position=1,
        stream_version=1,
        event_hash_byte=b"b",
    )
    duplicate_stream_version = _event_values(
        book_id=book_id,
        book_position=2,
        stream_id=stream_id,
        stream_version=1,
        event_hash_byte=b"c",
    )
    for values in (duplicate_position, duplicate_stream_version):
        with pytest.raises(IntegrityError):
            _insert_event(pg_engine, values)

    for field in ("previous_hash", "event_hash"):
        for bad_hash in (b"h" * 31, b"h" * 33):
            values = _event_values(
                book_id=book_id,
                book_position=2,
                event_hash_byte=b"d",
            )
            values[field] = bad_hash
            with pytest.raises(IntegrityError):
                _insert_event(pg_engine, values)

    invalid_payload = _event_values(
        book_id=book_id, book_position=2, event_hash_byte=b"e"
    )
    invalid_payload["payload"] = "[]"
    with pytest.raises(IntegrityError):
        _insert_event(pg_engine, invalid_payload)

    invalid_schema = _event_values(
        book_id=book_id, book_position=2, event_hash_byte=b"f"
    )
    invalid_schema["event_schema_version"] = 0
    with pytest.raises(IntegrityError):
        _insert_event(pg_engine, invalid_schema)


def test_event_causation_is_book_scoped_and_cannot_reference_self(pg_engine) -> None:
    first_book = uuid4()
    second_book = uuid4()
    _insert_book(pg_engine, first_book, "USD")
    _insert_book(pg_engine, second_book, "EUR")
    cause = _event_values(book_id=first_book, book_position=1, event_hash_byte=b"a")
    _insert_event(pg_engine, cause)

    cross_book = _event_values(
        book_id=second_book,
        book_position=1,
        causation_event_id=cause["event_id"],
        event_hash_byte=b"b",
    )
    with pytest.raises(IntegrityError):
        _insert_event(pg_engine, cross_book)

    self_id = uuid4()
    self_caused = _event_values(
        book_id=first_book,
        book_position=2,
        event_id=self_id,
        causation_event_id=self_id,
        event_hash_byte=b"c",
    )
    with pytest.raises(IntegrityError):
        _insert_event(pg_engine, self_caused)


def test_global_sequence_is_database_produced_unique_and_allows_rollback_gaps(
    pg_engine,
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    first = _event_values(book_id=book_id, book_position=1, event_hash_byte=b"a")
    _insert_event(pg_engine, first)

    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        rolled_back = _event_values(
            book_id=book_id, book_position=2, event_hash_byte=b"b"
        )
        _insert_event_on_connection(connection, rolled_back)
        transaction.rollback()
    finally:
        connection.close()

    after_gap = _event_values(book_id=book_id, book_position=2, event_hash_byte=b"c")
    _insert_event(pg_engine, after_gap)

    with pg_engine.connect() as connection:
        sequences = list(
            connection.execute(
                text(
                    """
                    select global_sequence
                      from ledger_events
                     where event_id in (:first_id, :after_id)
                     order by global_sequence
                    """
                ),
                {
                    "first_id": first["event_id"],
                    "after_id": after_gap["event_id"],
                },
            ).scalars()
        )
        generated_columns = connection.execute(
            text(
                """
                select recorded_at, global_sequence
                  from ledger_events
                 where event_id = :event_id
                """
            ),
            {"event_id": after_gap["event_id"]},
        ).one()

    assert sequences[1] > sequences[0] + 1
    assert generated_columns.recorded_at is not None
    assert generated_columns.global_sequence == sequences[1]


def test_book_and_stream_heads_are_event_bound_and_advance_monotonically(
    pg_engine, migrated_postgres_database
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    stream_id = uuid4()
    first = _event_values(
        book_id=book_id,
        book_position=1,
        stream_id=stream_id,
        stream_version=1,
        event_hash_byte=b"a",
    )
    second = _event_values(
        book_id=book_id,
        book_position=2,
        stream_id=stream_id,
        stream_version=2,
        event_hash_byte=b"b",
    )
    _insert_event(pg_engine, first)
    _insert_event(pg_engine, second)

    _execute(
        pg_engine,
        """
        insert into book_event_heads (book_id, last_position, last_hash)
        values (:book_id, 0, :zero_hash)
        """,
        {"book_id": book_id, "zero_hash": bytes(32)},
    )
    _execute(
        pg_engine,
        """
        update book_event_heads
           set last_position = 1, last_hash = :last_hash
         where book_id = :book_id
        """,
        {"book_id": book_id, "last_hash": first["event_hash"]},
    )
    _execute(
        pg_engine,
        """
        insert into event_stream_heads (
            book_id, stream_type, stream_id, last_version,
            last_book_position, last_event_id
        ) values (
            :book_id, :stream_type, :stream_id, 1, 1, :last_event_id
        )
        """,
        {
            "book_id": book_id,
            "stream_type": first["stream_type"],
            "stream_id": stream_id,
            "last_event_id": first["event_id"],
        },
    )
    _execute(
        pg_engine,
        """
        update event_stream_heads
           set last_version = 2,
               last_book_position = 2,
               last_event_id = :last_event_id
         where book_id = :book_id
           and stream_type = :stream_type
           and stream_id = :stream_id
        """,
        {
            "book_id": book_id,
            "stream_type": first["stream_type"],
            "stream_id": stream_id,
            "last_event_id": second["event_id"],
        },
    )

    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    try:
        for statement, parameters in (
            (
                """
                update book_event_heads
                   set last_position = 0, last_hash = :zero_hash
                 where book_id = :book_id
                """,
                {"book_id": book_id, "zero_hash": bytes(32)},
            ),
            (
                """
                update event_stream_heads
                   set last_version = 1,
                       last_book_position = 1,
                       last_event_id = :last_event_id
                 where book_id = :book_id
                   and stream_type = :stream_type
                   and stream_id = :stream_id
                """,
                {
                    "book_id": book_id,
                    "stream_type": first["stream_type"],
                    "stream_id": stream_id,
                    "last_event_id": first["event_id"],
                },
            ),
            (
                """
                update event_stream_heads
                   set stream_id = :replacement
                 where book_id = :book_id
                   and stream_type = :stream_type
                   and stream_id = :stream_id
                """,
                {
                    "book_id": book_id,
                    "stream_type": first["stream_type"],
                    "stream_id": stream_id,
                    "replacement": uuid4(),
                },
            ),
        ):
            with pytest.raises(DBAPIError):
                with owner_engine.begin() as connection:
                    connection.execute(
                        text(f'SET ROLE "{migrated_postgres_database.owner_role}"')
                    )
                    connection.execute(text(statement), parameters)
    finally:
        owner_engine.dispose()

    _rejects_integrity(
        pg_engine,
        """
        insert into event_stream_heads (
            book_id, stream_type, stream_id, last_version,
            last_book_position, last_event_id
        ) values (
            :book_id, 'investment_lot', :stream_id, 99, 2, :last_event_id
        )
        """,
        {
            "book_id": book_id,
            "stream_id": uuid4(),
            "last_event_id": second["event_id"],
        },
    )
