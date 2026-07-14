from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from track_anywhere.infrastructure.db.base import V2Base, load_v2_models

OUTBOX_INSERT_COLUMNS = {
    "message_id",
    "book_id",
    "source_event_id",
    "topic",
    "message_type",
    "dedupe_key",
    "payload",
    "available_at",
}
OUTBOX_UPDATE_COLUMNS = {
    "attempt_count",
    "available_at",
    "locked_by",
    "locked_until",
    "delivered_at",
    "last_error_code",
}


def _seed_event(connection, *, book_name: str = "Outbox book"):
    book_id = uuid4()
    event_id = uuid4()
    connection.execute(
        text(
            "insert into assets (asset_code, kind, ledger_scale, input_scale, display_scale, current_name, status) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active') on conflict do nothing"
        )
    )
    connection.execute(
        text(
            "insert into books (book_id, current_name, base_asset_code, write_state) values (:book_id, :book_name, 'USD', 'active')"
        ),
        {"book_id": book_id, "book_name": book_name},
    )
    connection.execute(
        text("""
        insert into ledger_events (
            event_id, book_id, book_position, stream_type, stream_id,
            stream_version, event_type, event_schema_version, command_id,
            actor_subject_id, correlation_id, effective_at, payload,
            previous_hash, event_hash
        ) values (
            :event_id, :book_id, 1, 'investment_lot', :stream_id,
            1, 'InvestmentLotAcquired', 1, :command_id,
            'human:test', :correlation_id, :effective_at, '{}'::jsonb,
            :previous_hash, :event_hash
        )
        """),
        {
            "event_id": event_id,
            "book_id": book_id,
            "stream_id": uuid4(),
            "command_id": uuid4(),
            "correlation_id": uuid4(),
            "effective_at": datetime.now(UTC),
            "previous_hash": b"p" * 32,
            "event_hash": bytes(str(book_id), "utf-8")[:32].ljust(32, b"e"),
        },
    )
    connection.execute(
        text("""
        insert into synchronous_projection_applied_events (
            book_id, event_id, projection_version
        ) values (:book_id, :event_id, 1)
        """),
        {"book_id": book_id, "event_id": event_id},
    )
    return book_id, event_id


def _insert_outbox(connection, *, book_id, event_id, key="stable-key", message_id=None):
    message_id = message_id or uuid4()
    connection.execute(
        text("""
        insert into outbox_messages (
            message_id, book_id, source_event_id, topic, message_type,
            dedupe_key, payload
        ) values (
            :message_id, :book_id, :event_id, 'ledger.events', 'posted',
            :key, '{}'::jsonb
        )
        """),
        {
            "message_id": message_id,
            "book_id": book_id,
            "event_id": event_id,
            "key": key,
        },
    )
    return message_id


def _expect_integrity_error(
    pg_engine,
    statement: str,
    params: dict[str, object],
    *,
    expected_sqlstate: str,
    message: str | None = None,
) -> str:
    with pytest.raises((DBAPIError, IntegrityError)) as error_info:
        with pg_engine.begin() as connection:
            connection.execute(text(statement), params)
    orig = getattr(error_info.value, "orig", None)
    sqlstate = str(getattr(orig, "sqlstate", ""))
    assert sqlstate == expected_sqlstate
    if message is not None:
        assert message in str(error_info.value)
    return sqlstate


def _column_privileges(connection, role: str, privilege: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            text("""
            select column_name
              from information_schema.column_privileges
             where grantee = :role
               and table_schema = 'public'
               and table_name = 'outbox_messages'
               and privilege_type = :privilege
            """),
            {"role": role, "privilege": privilege},
        )
    }


def test_outbox_model_and_schema_have_stable_unique_identities(pg_engine):
    load_v2_models()
    assert "outbox_messages" in V2Base.metadata.tables
    inspector = inspect(pg_engine)
    assert inspector.get_pk_constraint("outbox_messages")["constrained_columns"] == [
        "message_id"
    ]
    uniques = {
        tuple(u["column_names"])
        for u in inspector.get_unique_constraints("outbox_messages")
    }
    assert ("book_id", "topic", "dedupe_key") in uniques
    assert "exactly_once" not in {
        c["name"] for c in inspector.get_columns("outbox_messages")
    }
    index = next(
        index
        for index in inspector.get_indexes("outbox_messages")
        if index["name"] == "ix_outbox_messages_available"
    )
    assert "DELIVERED_AT IS NULL" in str(index.get("dialect_options", {})).upper()


def test_outbox_dedupe_is_book_scoped_and_rejects_same_book_duplicate(pg_engine):
    with pg_engine.begin() as connection:
        book_a, event_a = _seed_event(connection, book_name="A")
        book_b, event_b = _seed_event(connection, book_name="B")
        _insert_outbox(
            connection, book_id=book_a, event_id=event_a, key="same-business-key"
        )
        _insert_outbox(
            connection, book_id=book_b, event_id=event_b, key="same-business-key"
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into outbox_messages (
            message_id, book_id, source_event_id, topic, message_type,
            dedupe_key, payload
        ) values (
            :message_id, :book_id, :event_id, 'ledger.events', 'posted',
            'same-business-key', '{}'::jsonb
        )
        """,
        {"message_id": uuid4(), "book_id": book_a, "event_id": event_a},
        expected_sqlstate="23505",
        message="uq_outbox_messages_book_topic_dedupe",
    )


def test_outbox_rejects_cross_book_source_and_invalid_null_edges(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        other_book, _other_event = _seed_event(connection, book_name="Other")
        attempt_row = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="attempt"
        )
        partial_lock = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="partial"
        )
        blank_error = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="error"
        )
    cases = [
        (
            """
            insert into outbox_messages (message_id, book_id, source_event_id, topic, message_type, dedupe_key, payload)
            values (:message_id, :book_id, :event_id, 'ledger.events', 'posted', 'cross', '{}'::jsonb)
            """,
            {"message_id": uuid4(), "book_id": other_book, "event_id": event_id},
            {"23503"},
            None,
        ),
        (
            """
            update outbox_messages
               set attempt_count = -1
             where message_id = :message_id
            """,
            {"message_id": attempt_row},
            {"23514"},
            "outbox attempts cannot move backward",
        ),
        (
            """
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker'
             where message_id = :message_id
            """,
            {"message_id": partial_lock},
            {"23514"},
            "lock_complete",
        ),
        (
            """
            insert into outbox_messages (message_id, book_id, source_event_id, topic, message_type, dedupe_key, payload)
            values (:message_id, :book_id, :event_id, '', 'm', 'k3', '{}'::jsonb)
            """,
            {"message_id": uuid4(), "book_id": book_id, "event_id": event_id},
            {"23514"},
            "topic_nonblank",
        ),
        (
            """
            insert into outbox_messages (message_id, book_id, source_event_id, topic, message_type, dedupe_key, payload)
            values (:message_id, :book_id, :event_id, 't', 'm', 'k4', '[]'::jsonb)
            """,
            {"message_id": uuid4(), "book_id": book_id, "event_id": event_id},
            {"23514"},
            "payload_object",
        ),
        (
            """
            update outbox_messages
               set last_error_code = ''
             where message_id = :message_id
            """,
            {"message_id": blank_error},
            {"23514"},
            "error_code_nonblank",
        ),
    ]
    for statement, params, expected_sqlstate, message in cases:
        assert _expect_integrity_error(
            pg_engine,
            statement,
            params,
            expected_sqlstate=next(iter(expected_sqlstate)),
            message=message,
        )


def test_outbox_delivery_is_terminal_and_attempts_do_not_regress(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        message_id = _insert_outbox(connection, book_id=book_id, event_id=event_id)
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 2,
                   locked_by = 'worker-a',
                   locked_until = :locked_until
             where message_id = :message_id
            """),
            {
                "message_id": message_id,
                "locked_until": datetime.now(UTC) + timedelta(minutes=5),
            },
        )
        connection.execute(
            text("""
            update outbox_messages
               set delivered_at = :delivered_at,
                   locked_by = null,
                   locked_until = null
             where message_id = :message_id
            """),
            {"message_id": message_id, "delivered_at": datetime.now(UTC)},
        )
    assert _expect_integrity_error(
        pg_engine,
        "update outbox_messages set delivered_at = null where message_id = :message_id",
        {"message_id": message_id},
        expected_sqlstate="23514",
        message="delivered outbox message is terminal",
    )
    assert _expect_integrity_error(
        pg_engine,
        "update outbox_messages set attempt_count = 1 where message_id = :message_id",
        {"message_id": message_id},
        expected_sqlstate="23514",
        message="outbox attempts cannot move backward",
    )
    assert _expect_integrity_error(
        pg_engine,
        "update outbox_messages set locked_by = 'worker-b', locked_until = :until where message_id = :message_id",
        {"message_id": message_id, "until": datetime.now(UTC) + timedelta(minutes=5)},
        expected_sqlstate="23514",
        message="delivered outbox message is terminal",
    )


def test_outbox_lock_requires_positive_attempt(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        message_id = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="zero-lock"
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        update outbox_messages
           set attempt_count = 0, locked_by = 'worker', locked_until = :future
         where message_id = :message_id
        """,
        {"message_id": message_id, "future": datetime.now(UTC) + timedelta(minutes=5)},
        expected_sqlstate="23514",
        message="outbox claim owner change must increment attempts",
    )


def test_outbox_delivery_requires_valid_claim_and_zero_error(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        no_claim = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="no-claim"
        )
        expired = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="expired"
        )
        keep_error = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="error"
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker', locked_until = :expired
             where message_id = :message_id
            """),
            {
                "message_id": expired,
                "expired": datetime.now(UTC) - timedelta(seconds=1),
            },
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker', locked_until = :future, last_error_code = 'RETRY'
             where message_id = :message_id
            """),
            {
                "message_id": keep_error,
                "future": datetime.now(UTC) + timedelta(minutes=5),
            },
        )
    for message_id, expected in (
        (no_claim, "outbox delivery requires an active claim"),
        (expired, "outbox delivery requires an unexpired claim"),
    ):
        assert _expect_integrity_error(
            pg_engine,
            "update outbox_messages set delivered_at = :now where message_id = :message_id",
            {"message_id": message_id, "now": datetime.now(UTC)},
            expected_sqlstate="23514",
            message=expected,
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        update outbox_messages
           set delivered_at = :now, locked_by = null, locked_until = null
         where message_id = :message_id
        """,
        {"message_id": keep_error, "now": datetime.now(UTC)},
        expected_sqlstate="23514",
        message="outbox delivery must clear last error",
    )


def test_outbox_claim_owner_change_requires_attempt_increment(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        message_id = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="claim"
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker-a', locked_until = :future
             where message_id = :message_id
            """),
            {
                "message_id": message_id,
                "future": datetime.now(UTC) + timedelta(minutes=5),
            },
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        update outbox_messages
           set attempt_count = 2, locked_by = 'worker-b', locked_until = :future
         where message_id = :message_id
        """,
        {"message_id": message_id, "future": datetime.now(UTC) + timedelta(minutes=5)},
        expected_sqlstate="23514",
        message="unexpired outbox claim cannot be stolen",
    )
    with pg_engine.begin() as connection:
        expired_message_id = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="expired-claim"
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker-a', locked_until = :past
             where message_id = :message_id
            """),
            {
                "message_id": expired_message_id,
                "past": datetime.now(UTC) - timedelta(seconds=1),
            },
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 2, locked_by = 'worker-b', locked_until = :future
             where message_id = :message_id
            """),
            {
                "message_id": expired_message_id,
                "future": datetime.now(UTC) + timedelta(minutes=5),
            },
        )


def test_unexpired_outbox_claim_cannot_be_shortened(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        message_id = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="shorten-claim"
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker-a', locked_until = :future
             where message_id = :message_id
            """),
            {
                "message_id": message_id,
                "future": datetime.now(UTC) + timedelta(minutes=5),
            },
        )

    assert _expect_integrity_error(
        pg_engine,
        """
        update outbox_messages
           set locked_until = :past
         where message_id = :message_id
        """,
        {"message_id": message_id, "past": datetime.now(UTC) - timedelta(seconds=1)},
        expected_sqlstate="23514",
        message="unexpired outbox claim cannot be shortened or released",
    )


def test_unexpired_outbox_claim_cannot_be_released(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        message_id = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="release-claim"
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker-a', locked_until = :future
             where message_id = :message_id
            """),
            {
                "message_id": message_id,
                "future": datetime.now(UTC) + timedelta(minutes=5),
            },
        )

    assert _expect_integrity_error(
        pg_engine,
        """
        update outbox_messages
           set locked_by = null, locked_until = null
         where message_id = :message_id
        """,
        {"message_id": message_id},
        expected_sqlstate="23514",
        message="unexpired outbox claim cannot be shortened or released",
    )


def test_delivered_outbox_row_is_fully_terminal(pg_engine):
    with pg_engine.begin() as connection:
        book_id, event_id = _seed_event(connection)
        message_id = _insert_outbox(
            connection, book_id=book_id, event_id=event_id, key="terminal"
        )
        connection.execute(
            text("""
            update outbox_messages
               set attempt_count = 1, locked_by = 'worker', locked_until = :future
             where message_id = :message_id
            """),
            {
                "message_id": message_id,
                "future": datetime.now(UTC) + timedelta(minutes=5),
            },
        )
        connection.execute(
            text("""
            update outbox_messages
               set delivered_at = :now, locked_by = null, locked_until = null, last_error_code = null
             where message_id = :message_id
            """),
            {"message_id": message_id, "now": datetime.now(UTC)},
        )
    for statement in (
        "update outbox_messages set attempt_count = attempt_count + 1 where message_id = :message_id",
        "update outbox_messages set available_at = :now where message_id = :message_id",
        "update outbox_messages set last_error_code = 'LATE' where message_id = :message_id",
    ):
        assert _expect_integrity_error(
            pg_engine,
            statement,
            {"message_id": message_id, "now": datetime.now(UTC)},
            expected_sqlstate="23514",
            message="delivered outbox message is terminal",
        )


def test_outbox_runtime_acl_is_exact_and_identity_source_payload_are_immutable(
    migrated_postgres_database,
):
    runtime = migrated_postgres_database.runtime_role
    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            assert (
                _column_privileges(connection, runtime, "INSERT")
                == OUTBOX_INSERT_COLUMNS
            )
            assert (
                _column_privileges(connection, runtime, "UPDATE")
                == OUTBOX_UPDATE_COLUMNS
            )
            for privilege in (
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
                "MAINTAIN",
            ):
                assert not connection.execute(
                    text(
                        "select has_table_privilege(:role, 'public.outbox_messages', :privilege)"
                    ),
                    {"role": runtime, "privilege": privilege},
                ).scalar_one()
    finally:
        engine.dispose()
