from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError


def _insert_book(engine: Engine, book_id: UUID, asset_code: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values (
                    :asset_code, 'fiat', 2, 2, 2, 'Test asset', 'active'
                ) on conflict (asset_code) do nothing
                """
            ),
            {"asset_code": asset_code},
        )
        connection.execute(
            text(
                """
                insert into books (
                    book_id, current_name, base_asset_code, write_state
                ) values (:book_id, 'Receipt book', :asset_code, 'active')
                """
            ),
            {"book_id": book_id, "asset_code": asset_code},
        )


def _insert_event(
    engine: Engine,
    book_id: UUID,
    position: int,
    *,
    command_id: UUID | None = None,
) -> UUID:
    event_id = uuid4()
    event_command_id = command_id or uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into ledger_events (
                    event_id, book_id, book_position, stream_type, stream_id,
                    stream_version, event_type, event_schema_version, command_id,
                    actor_subject_id, correlation_id, causation_event_id,
                    effective_at, payload, previous_hash, event_hash
                ) values (
                    :event_id, :book_id, :position, 'investment_lot',
                    :stream_id, 1, 'InvestmentLotAcquired', 1, :command_id,
                    'human:test-user', :correlation_id, null, :effective_at,
                    '{}'::jsonb, :previous_hash, :event_hash
                )
                """
            ),
            {
                "event_id": event_id,
                "book_id": book_id,
                "position": position,
                "stream_id": uuid4(),
                "command_id": event_command_id,
                "correlation_id": uuid4(),
                "effective_at": datetime.now(UTC),
                "previous_hash": b"p" * 32,
                "event_hash": bytes([position]) * 32,
            },
        )
        connection.execute(
            text(
                """
                insert into synchronous_projection_applied_events (
                    book_id, event_id, projection_version
                ) values (:book_id, :event_id, 1)
                """
            ),
            {"book_id": book_id, "event_id": event_id},
        )
    return event_id


def _receipt_values(
    book_id: UUID,
    *,
    key_hash: bytes = b"k" * 32,
    request_hash: bytes = b"r" * 32,
    command_id: UUID | None = None,
) -> dict[str, object]:
    return {
        "actor_subject_id": "human:test-user",
        "book_id": book_id,
        "operation": "post-transaction",
        "idempotency_key_hash": key_hash,
        "request_hash": request_hash,
        "command_id": command_id or uuid4(),
    }


def _insert_processing(connection: Connection, values: dict[str, object]) -> None:
    connection.execute(
        text(
            """
            insert into command_receipts (
                actor_subject_id, book_id, operation, idempotency_key_hash,
                request_hash, command_id, status
            ) values (
                :actor_subject_id, :book_id, :operation, :idempotency_key_hash,
                :request_hash, :command_id, 'processing'
            )
            """
        ),
        values,
    )


def _complete(
    connection: Connection,
    values: dict[str, object],
    *,
    first_position: int | None = None,
    last_position: int | None = None,
) -> None:
    connection.execute(
        text(
            """
            update command_receipts
               set status = 'completed',
                   response_schema_version = 1,
                   result_status = 201,
                   result_body = '{"ok": true}'::jsonb,
                   first_book_position = :first_position,
                   last_book_position = :last_position,
                   completed_at = clock_timestamp()
             where actor_subject_id = :actor_subject_id
               and book_id = :book_id
               and operation = :operation
               and idempotency_key_hash = :idempotency_key_hash
            """
        ),
        {**values, "first_position": first_position, "last_position": last_position},
    )


def _insert_completed(
    engine: Engine,
    values: dict[str, object],
    *,
    first_position: int | None = None,
    last_position: int | None = None,
) -> None:
    with engine.begin() as connection:
        _insert_processing(connection, values)
        _complete(
            connection,
            values,
            first_position=first_position,
            last_position=last_position,
        )


def test_receipt_scope_is_hashed_unique_and_contains_no_raw_key_column(
    pg_engine,
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    values = _receipt_values(book_id)
    _insert_completed(pg_engine, values)

    duplicate = {**values, "request_hash": b"s" * 32, "command_id": uuid4()}
    with pytest.raises(IntegrityError):
        _insert_completed(pg_engine, duplicate)

    with pg_engine.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    """
                    select column_name
                      from information_schema.columns
                     where table_schema = 'public'
                       and table_name = 'command_receipts'
                    """
                )
            ).scalars()
        )

    assert "idempotency_key_hash" in columns
    assert not {"idempotency_key", "raw_idempotency_key"} & columns


def test_receipts_reject_bad_hashes_and_incomplete_completed_shape(pg_engine) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")

    for field in ("idempotency_key_hash", "request_hash"):
        for bad_hash in (b"h" * 31, b"h" * 33):
            values = _receipt_values(book_id)
            values[field] = bad_hash
            with pytest.raises(IntegrityError):
                with pg_engine.begin() as connection:
                    _insert_processing(connection, values)

    values = _receipt_values(book_id, key_hash=b"m" * 32)
    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            _insert_processing(connection, values)
            connection.execute(
                text(
                    """
                    update command_receipts
                       set status = 'completed'
                     where actor_subject_id = :actor_subject_id
                       and book_id = :book_id
                       and operation = :operation
                       and idempotency_key_hash = :idempotency_key_hash
                    """
                ),
                values,
            )


@pytest.mark.parametrize(
    "missing_field",
    (
        "response_schema_version",
        "result_status",
        "result_body",
        "completed_at",
    ),
)
def test_completed_receipt_requires_every_response_field(
    pg_engine, missing_field: str
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    values = _receipt_values(book_id)
    completed_values = {
        "response_schema_version": "1",
        "result_status": "201",
        "result_body": "'{\"ok\": true}'::jsonb",
        "completed_at": "clock_timestamp()",
    }
    completed_values[missing_field] = "null"

    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            _insert_processing(connection, values)
            connection.execute(
                text(
                    f"""
                    update command_receipts
                       set status = 'completed',
                           response_schema_version =
                               {completed_values["response_schema_version"]},
                           result_status = {completed_values["result_status"]},
                           result_body = {completed_values["result_body"]},
                           completed_at = {completed_values["completed_at"]}
                     where actor_subject_id = :actor_subject_id
                       and book_id = :book_id
                       and operation = :operation
                       and idempotency_key_hash = :idempotency_key_hash
                    """
                ),
                values,
            )


def test_completed_receipt_rejects_json_null_response_body(pg_engine) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    values = _receipt_values(book_id)

    with pytest.raises(IntegrityError):
        with pg_engine.begin() as connection:
            _insert_processing(connection, values)
            connection.execute(
                text(
                    """
                    update command_receipts
                       set status = 'completed',
                           response_schema_version = 1,
                           result_status = 201,
                           result_body = 'null'::jsonb,
                           completed_at = clock_timestamp()
                     where actor_subject_id = :actor_subject_id
                       and book_id = :book_id
                       and operation = :operation
                       and idempotency_key_hash = :idempotency_key_hash
                    """
                ),
                values,
            )


@pytest.mark.parametrize("range_case", ("other_commands", "position_gap"))
def test_completed_receipt_range_is_contiguous_and_owned_by_its_command(
    pg_engine, range_case: str
) -> None:
    book_id = uuid4()
    command_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    if range_case == "other_commands":
        _insert_event(pg_engine, book_id, 1, command_id=uuid4())
        _insert_event(pg_engine, book_id, 2, command_id=uuid4())
        last_position = 2
    else:
        _insert_event(pg_engine, book_id, 1, command_id=command_id)
        _insert_event(pg_engine, book_id, 3, command_id=command_id)
        last_position = 3

    values = _receipt_values(book_id, command_id=command_id)
    with pytest.raises(IntegrityError):
        _insert_completed(
            pg_engine,
            values,
            first_position=1,
            last_position=last_position,
        )


def test_completed_receipt_accepts_contiguous_events_from_its_command(
    pg_engine,
) -> None:
    book_id = uuid4()
    command_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    for position in (1, 2, 3):
        _insert_event(pg_engine, book_id, position, command_id=command_id)

    values = _receipt_values(book_id, command_id=command_id)
    _insert_completed(pg_engine, values, first_position=1, last_position=3)

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                select count(*)
                  from ledger_events event
                  join command_receipts receipt
                    on receipt.book_id = event.book_id
                   and receipt.command_id = event.command_id
                   and event.book_position between
                       receipt.first_book_position and receipt.last_book_position
                 where receipt.actor_subject_id = :actor_subject_id
                   and receipt.book_id = :book_id
                   and receipt.operation = :operation
                   and receipt.idempotency_key_hash = :idempotency_key_hash
                """
                ),
                values,
            ).scalar_one()
            == 3
        )


def test_processing_is_deferred_until_commit_and_rechecks_current_receipt(
    pg_engine,
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    abandoned = _receipt_values(book_id, key_hash=b"a" * 32)

    with pytest.raises(DBAPIError):
        with pg_engine.begin() as connection:
            _insert_processing(connection, abandoned)

    completed = _receipt_values(book_id, key_hash=b"b" * 32)
    _insert_completed(pg_engine, completed)

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                    select status::text
                      from command_receipts
                     where actor_subject_id = :actor_subject_id
                       and book_id = :book_id
                       and operation = :operation
                       and idempotency_key_hash = :idempotency_key_hash
                    """
                ),
                completed,
            ).scalar_one()
            == "completed"
        )


def test_receipt_positions_are_paired_book_scoped_and_reference_real_events(
    pg_engine,
) -> None:
    first_book = uuid4()
    second_book = uuid4()
    range_command_id = uuid4()
    _insert_book(pg_engine, first_book, "USD")
    _insert_book(pg_engine, second_book, "EUR")
    _insert_event(pg_engine, first_book, 1, command_id=range_command_id)
    _insert_event(pg_engine, first_book, 2, command_id=range_command_id)
    _insert_event(pg_engine, second_book, 3)

    _insert_completed(
        pg_engine,
        _receipt_values(first_book, key_hash=b"v" * 32, command_id=range_command_id),
        first_position=1,
        last_position=2,
    )

    invalid_pairs = (
        (1, None),
        (None, 2),
        (2, 1),
        (1, 99),
        (1, 3),
    )
    for index, (first_position, last_position) in enumerate(invalid_pairs, start=1):
        values = _receipt_values(first_book, key_hash=bytes([index]) * 32)
        with pytest.raises(IntegrityError):
            _insert_completed(
                pg_engine,
                values,
                first_position=first_position,
                last_position=last_position,
            )


def test_receipt_scope_is_frozen_and_completion_is_terminal(
    pg_engine, migrated_postgres_database
) -> None:
    book_id = uuid4()
    _insert_book(pg_engine, book_id, "USD")
    values = _receipt_values(book_id)
    _insert_completed(pg_engine, values)

    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    try:
        for assignment in (
            "request_hash = decode(repeat('78', 32), 'hex')",
            "command_id = gen_random_uuid()",
            "created_at = clock_timestamp()",
            "status = 'processing'",
            "result_status = 200",
        ):
            with pytest.raises(DBAPIError):
                with owner_engine.begin() as connection:
                    connection.execute(
                        text(f'SET ROLE "{migrated_postgres_database.owner_role}"')
                    )
                    connection.execute(
                        text(
                            f"""
                            update command_receipts set {assignment}
                             where actor_subject_id = :actor_subject_id
                               and book_id = :book_id
                               and operation = :operation
                               and idempotency_key_hash = :idempotency_key_hash
                            """
                        ),
                        values,
                    )
    finally:
        owner_engine.dispose()
