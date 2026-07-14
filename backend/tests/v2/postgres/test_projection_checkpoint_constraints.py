from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from track_anywhere.infrastructure.db.base import V2Base, load_v2_models

ASYNC_TABLES = (
    "projection_checkpoints",
    "projection_generations",
    "projection_dirty_periods",
    "projection_failures",
)
RUNTIME_INSERT_COLUMNS = {
    "projection_checkpoints": {
        "projection_name",
        "projector_version",
        "book_id",
        "last_book_position",
        "active_generation",
        "lease_owner",
        "lease_expires_at",
    },
    "projection_generations": {
        "projection_name",
        "projector_version",
        "book_id",
        "generation",
        "state",
        "rebuild_from_position",
        "last_book_position",
        "target_book_position",
    },
    "projection_dirty_periods": {
        "projection_name",
        "projector_version",
        "book_id",
        "generation",
        "period_start",
        "period_end",
        "source_event_id",
        "source_book_position",
    },
    "projection_failures": {
        "failure_id",
        "projection_name",
        "projector_version",
        "book_id",
        "generation",
        "source_event_id",
        "source_book_position",
        "event_type",
        "event_schema_version",
        "failure_kind",
        "retry_state",
        "attempt_count",
        "next_retry_at",
        "last_error_code",
    },
}

TRIGGER_FUNCTIONS = {
    "v2_touch_projection_checkpoint",
    "v2_validate_active_projection_generation",
    "v2_validate_projection_generation_update",
    "v2_validate_dirty_period_source",
    "v2_validate_projection_failure",
    "v2_validate_outbox_update",
}

RUNTIME_UPDATE_COLUMNS = {
    "projection_checkpoints": {
        "last_book_position",
        "active_generation",
        "lease_owner",
        "lease_expires_at",
    },
    "projection_generations": {
        "state",
        "last_book_position",
        "target_book_position",
    },
    "projection_dirty_periods": {"source_event_id", "source_book_position"},
    "projection_failures": {
        "retry_state",
        "attempt_count",
        "next_retry_at",
        "last_error_code",
        "resolved_at",
    },
}


def _event_params(
    book_id, event_id, position, event_type="InvestmentLotAcquired", version=1
):
    return {
        "event_id": event_id,
        "book_id": book_id,
        "book_position": position,
        "stream_id": uuid4(),
        "stream_version": position,
        "event_type": event_type,
        "event_schema_version": version,
        "command_id": uuid4(),
        "correlation_id": uuid4(),
        "effective_at": datetime.now(UTC),
        "previous_hash": bytes([position]) * 32,
        "event_hash": bytes([position + 10]) * 32,
    }


def _seed_book_with_events(connection, *, event_count: int = 2):
    book_id = uuid4()
    connection.execute(
        text(
            "insert into assets (asset_code, kind, ledger_scale, input_scale, display_scale, current_name, status) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active') on conflict do nothing"
        )
    )
    connection.execute(
        text(
            "insert into books (book_id, current_name, base_asset_code, write_state) values (:book_id, 'Async book', 'USD', 'active')"
        ),
        {"book_id": book_id},
    )
    events = []
    for position in range(1, event_count + 1):
        event_id = uuid4()
        connection.execute(
            text("""
            insert into ledger_events (
                event_id, book_id, book_position, stream_type, stream_id,
                stream_version, event_type, event_schema_version, command_id,
                actor_subject_id, correlation_id, effective_at, payload,
                previous_hash, event_hash
            ) values (
                :event_id, :book_id, :book_position, 'investment_lot', :stream_id,
                :stream_version, :event_type, :event_schema_version, :command_id,
                'human:test', :correlation_id, :effective_at, '{}'::jsonb,
                :previous_hash, :event_hash
            )
            """),
            _event_params(book_id, event_id, position),
        )
        connection.execute(
            text("""
            insert into synchronous_projection_applied_events (
                book_id, event_id, projection_version
            ) values (:book_id, :event_id, 1)
            """),
            {"book_id": book_id, "event_id": event_id},
        )
        events.append(event_id)
    return book_id, events


def _seed_active_projection(connection):
    book_id, events = _seed_book_with_events(connection)
    connection.execute(
        text("""
        insert into projection_generations (
            projection_name, projector_version, book_id, generation, state,
            last_book_position, target_book_position
        ) values ('monthly', 1, :book_id, 1, 'building', 0, 1)
        """),
        {"book_id": book_id},
    )
    connection.execute(
        text("""
        update projection_generations
           set state = 'catching_up', last_book_position = 1
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 1
        """),
        {"book_id": book_id},
    )
    connection.execute(
        text("""
        update projection_generations
           set state = 'active'
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 1
        """),
        {"book_id": book_id},
    )
    connection.execute(
        text("""
        insert into projection_checkpoints (
            projection_name, projector_version, book_id, last_book_position,
            active_generation
        ) values ('monthly', 1, :book_id, 1, 1)
        """),
        {"book_id": book_id},
    )
    return book_id, events


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


def _column_privileges(
    connection, role: str, table_name: str, privilege: str
) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            text("""
            select column_name
              from information_schema.column_privileges
             where grantee = :role
               and table_schema = 'public'
               and table_name = :table_name
               and privilege_type = :privilege
            """),
            {"role": role, "table_name": table_name, "privilege": privilege},
        )
    }


def test_checkpoint_cannot_be_global_only(pg_engine):
    inspector = inspect(pg_engine)
    pk = inspector.get_pk_constraint("projection_checkpoints")["constrained_columns"]
    assert pk == ["projection_name", "projector_version", "book_id"]
    assert "last_global_position" not in {
        c["name"] for c in inspector.get_columns("projection_checkpoints")
    }


def test_async_projection_tables_are_in_model_metadata() -> None:
    load_v2_models()
    assert set(ASYNC_TABLES).issubset(V2Base.metadata.tables)


def test_active_generation_swap_is_deferrable_and_retains_old_generation(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_active_projection(connection)
        connection.execute(
            text("""
            insert into projection_generations (
                projection_name, projector_version, book_id, generation, state,
                last_book_position, target_book_position
            ) values ('monthly', 1, :book_id, 2, 'building', 0, 2)
            """),
            {"book_id": book_id},
        )
        old_activated_at = connection.execute(
            text(
                "select activated_at from projection_generations where book_id = :book_id and generation = 1"
            ),
            {"book_id": book_id},
        ).scalar_one()
        connection.execute(
            text("""
            update projection_generations
               set state = 'catching_up', last_book_position = 2
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 2
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_generations
               set state = 'retired'
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 1
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_generations
               set state = 'active'
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 2
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_checkpoints
               set active_generation = 2, last_book_position = 2
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id
            """),
            {"book_id": book_id},
        )
        retired = connection.execute(
            text(
                "select activated_at, completed_at from projection_generations where book_id = :book_id and generation = 1"
            ),
            {"book_id": book_id},
        ).one()
        assert retired.activated_at == old_activated_at
        assert retired.completed_at is not None


def test_generation_cannot_activate_before_reaching_target(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_book_with_events(connection)
        connection.execute(
            text("""
            insert into projection_generations (
                projection_name, projector_version, book_id, generation, state,
                last_book_position, target_book_position
            ) values ('monthly', 1, :book_id, 1, 'building', 0, 2)
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_generations
               set state = 'catching_up', last_book_position = 1
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 1
            """),
            {"book_id": book_id},
        )

    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set state = 'active'
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="active projection generation must reach its target",
    )


def test_dangling_pointer_multiple_active_and_progress_regressions_fail(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_active_projection(connection)
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_checkpoints
           set active_generation = 99
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id
        """,
        {"book_id": book_id},
        expected_sqlstate="23503",
        message="fk_projection_checkpoints_active_generation",
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            insert into projection_generations (
                projection_name, projector_version, book_id, generation, state,
                last_book_position, target_book_position
            ) values ('monthly', 1, :book_id, 2, 'building', 0, 2)
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_generations
               set state = 'catching_up', last_book_position = 2
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 2
            """),
            {"book_id": book_id},
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set state = 'active'
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 2
        """,
        {"book_id": book_id},
        expected_sqlstate="23505",
        message="ux_projection_generations_one_active",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_checkpoints
           set last_book_position = 0
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection checkpoint cannot move backward",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set last_book_position = 0
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation progress cannot move backward",
    )


def test_invalid_lifecycle_and_failure_retry_edges_fail(pg_engine):
    with pg_engine.begin() as connection:
        book_id, events = _seed_active_projection(connection)
    bad_cases = [
        (
            """
            insert into projection_generations (
                projection_name, projector_version, book_id, generation, state,
                target_book_position
            ) values ('monthly', 1, :book_id, 3, 'catching_up', 2)
            """,
            {"book_id": book_id},
        ),
        (
            """
            insert into projection_failures (
                failure_id, projection_name, projector_version, book_id, generation,
                source_event_id, source_book_position, event_type,
                event_schema_version, failure_kind, retry_state, last_error_code
            ) values (:failure_id, 'monthly', 1, :book_id, 1, :event_id, 1,
                'InvestmentLotAcquired', 1, 'unknown_event', 'ready', 'E')
            """,
            {"failure_id": uuid4(), "book_id": book_id, "event_id": events[0]},
        ),
    ]
    for statement, params, expected_message in (
        (*bad_cases[0], "projection generation must start in building state"),
        (*bad_cases[1], "ready_has_next_retry"),
    ):
        assert _expect_integrity_error(
            pg_engine,
            statement,
            params,
            expected_sqlstate="23514",
            message=expected_message,
        )


def test_source_identity_rejects_same_book_cross_position_splice_and_type_forgery(
    pg_engine,
):
    with pg_engine.begin() as connection:
        book_id, events = _seed_active_projection(connection)
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into projection_dirty_periods (
            projection_name, projector_version, book_id, generation,
            period_start, period_end, source_event_id, source_book_position
        ) values ('monthly', 1, :book_id, 1, :start, :end, :event_id, 2)
        """,
        {
            "book_id": book_id,
            "event_id": events[0],
            "start": date(2026, 1, 1),
            "end": date(2026, 2, 1),
        },
        expected_sqlstate="23514",
        message="dirty period must bind its exact source event",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into projection_failures (
            failure_id, projection_name, projector_version, book_id, generation,
            source_event_id, source_book_position, event_type,
            event_schema_version, failure_kind, last_error_code
        ) values (:failure_id, 'monthly', 1, :book_id, 1, :event_id, 1,
            'ForgedType', 1, 'unknown_event', 'E')
        """,
        {"failure_id": uuid4(), "book_id": book_id, "event_id": events[0]},
        expected_sqlstate="23514",
        message="projection failure must bind its exact source event",
    )


def test_dirty_period_delete_allowed_but_source_update_cannot_regress(pg_engine):
    with pg_engine.begin() as connection:
        book_id, events = _seed_active_projection(connection)
        connection.execute(
            text("""
            insert into projection_dirty_periods (
                projection_name, projector_version, book_id, generation,
                period_start, period_end, source_event_id, source_book_position
            ) values ('monthly', 1, :book_id, 1, :start, :end, :event_id, 2)
            """),
            {
                "book_id": book_id,
                "event_id": events[1],
                "start": date(2026, 1, 1),
                "end": date(2026, 2, 1),
            },
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_dirty_periods
           set source_event_id = :event_id, source_book_position = 1
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id, "event_id": events[0]},
        expected_sqlstate="23514",
        message="dirty period source position cannot move backward",
    )
    with pg_engine.begin() as connection:
        deleted = connection.execute(
            text("delete from projection_dirty_periods where book_id = :book_id"),
            {"book_id": book_id},
        ).rowcount
    assert deleted == 1


def test_failure_is_generation_scoped_and_attempt_count_cannot_regress(pg_engine):
    with pg_engine.begin() as connection:
        book_id, events = _seed_active_projection(connection)
        connection.execute(
            text("""
            insert into projection_failures (
                failure_id, projection_name, projector_version, book_id, generation,
                source_event_id, source_book_position, event_type,
                event_schema_version, failure_kind, attempt_count, last_error_code
            ) values (:failure_id, 'monthly', 1, :book_id, 1, :event_id, 1,
                'InvestmentLotAcquired', 1, 'unknown_event', 2, 'UNKNOWN_SCHEMA')
            """),
            {"failure_id": uuid4(), "book_id": book_id, "event_id": events[0]},
        )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_failures
           set attempt_count = 1
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection failure attempts cannot move backward",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into projection_failures (
            failure_id, projection_name, projector_version, book_id, generation,
            source_event_id, source_book_position, event_type,
            event_schema_version, failure_kind, last_error_code
        ) values (:failure_id, 'missing', 1, :book_id, 1, :event_id, 1,
            'InvestmentLotAcquired', 1, 'unknown_event', 'E')
        """,
        {"failure_id": uuid4(), "book_id": book_id, "event_id": events[0]},
        expected_sqlstate="23503",
        message="fk_projection_failures_generation",
    )


def test_checkpoint_and_generation_cursor_must_match_and_exist(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_active_projection(connection)
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_checkpoints
           set last_book_position = 2
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="checkpoint cursor must match the active generation",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set last_book_position = 999
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation cursor must reference an existing Book event",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set target_book_position = 999
         where projection_name = 'monthly' and projector_version = 1
           and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation target must reference an existing Book event",
    )

    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text("""
            update projection_generations
               set last_book_position = 2, target_book_position = 2
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 1
            """),
            {"book_id": book_id},
        )
        with pytest.raises((DBAPIError, IntegrityError)) as error_info:
            transaction.commit()
        assert getattr(error_info.value.orig, "sqlstate", "") == "23514"
        assert "checkpoint cursor must match the active generation" in str(
            error_info.value
        )
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_generation_insert_positive_cursor_and_target_must_exist(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_book_with_events(connection)
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into projection_generations (
            projection_name, projector_version, book_id, generation, state,
            last_book_position, target_book_position
        ) values ('monthly', 1, :book_id, 1, 'building', 999, 999)
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation cursor must reference an existing Book event",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into projection_generations (
            projection_name, projector_version, book_id, generation, state,
            last_book_position, target_book_position
        ) values ('monthly', 1, :book_id, 1, 'building', 0, 999)
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation target must reference an existing Book event",
    )


def test_orphan_active_generation_and_target_regressions_fail(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_book_with_events(connection)
    connection = pg_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text("""
            insert into projection_generations (
                projection_name, projector_version, book_id, generation, state,
                last_book_position, target_book_position
            ) values ('monthly', 1, :book_id, 1, 'building', 0, 2)
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_generations
               set state = 'catching_up', last_book_position = 2
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 1
            """),
            {"book_id": book_id},
        )
        connection.execute(
            text("""
            update projection_generations
               set state = 'active'
             where projection_name = 'monthly' and projector_version = 1
               and book_id = :book_id and generation = 1
            """),
            {"book_id": book_id},
        )
        with pytest.raises((DBAPIError, IntegrityError)) as error_info:
            transaction.commit()
        assert getattr(error_info.value.orig, "sqlstate", "") == "23514"
        assert "active generation requires a checkpoint" in str(error_info.value)
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
    with pg_engine.begin() as connection:
        book_id, _events = _seed_active_projection(connection)
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set target_book_position = 0
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation target cannot move backward",
    )
    assert _expect_integrity_error(
        pg_engine,
        """
        update projection_generations
           set target_book_position = 999
         where projection_name = 'monthly' and projector_version = 1 and book_id = :book_id and generation = 1
        """,
        {"book_id": book_id},
        expected_sqlstate="23514",
        message="projection generation target must reference an existing Book event",
    )


def test_same_source_event_can_fail_in_active_and_shadow_generations(pg_engine):
    with pg_engine.begin() as connection:
        book_id, events = _seed_active_projection(connection)
        connection.execute(
            text("""
            insert into projection_generations (
                projection_name, projector_version, book_id, generation, state,
                last_book_position, target_book_position
            ) values ('monthly', 1, :book_id, 2, 'building', 0, 2)
            """),
            {"book_id": book_id},
        )
        for generation in (1, 2):
            connection.execute(
                text("""
                insert into projection_failures (
                    failure_id, projection_name, projector_version, book_id, generation,
                    source_event_id, source_book_position, event_type,
                    event_schema_version, failure_kind, attempt_count, last_error_code
                ) values (:failure_id, 'monthly', 1, :book_id, :generation, :event_id, 1,
                    'InvestmentLotAcquired', 1, 'unknown_event', 1, 'UNKNOWN_SCHEMA')
                """),
                {
                    "failure_id": uuid4(),
                    "book_id": book_id,
                    "generation": generation,
                    "event_id": events[0],
                },
            )
    assert _expect_integrity_error(
        pg_engine,
        """
        insert into projection_failures (
            failure_id, projection_name, projector_version, book_id, generation,
            source_event_id, source_book_position, event_type,
            event_schema_version, failure_kind, last_error_code
        ) values (:failure_id, 'monthly', 1, :book_id, 99, :event_id, 1,
            'InvestmentLotAcquired', 1, 'unknown_event', 'E')
        """,
        {"failure_id": uuid4(), "book_id": book_id, "event_id": events[0]},
        expected_sqlstate="23503",
        message="fk_projection_failures_generation",
    )


def test_checkpoint_updated_at_is_database_maintained(pg_engine):
    with pg_engine.begin() as connection:
        book_id, _events = _seed_active_projection(connection)
        before = connection.execute(
            text(
                "select updated_at from projection_checkpoints where book_id = :book_id"
            ),
            {"book_id": book_id},
        ).scalar_one()
        connection.execute(text("select pg_sleep(0.001)"))
        connection.execute(
            text(
                "update projection_checkpoints set lease_owner = 'worker-a', lease_expires_at = now() + interval '5 minutes' where book_id = :book_id"
            ),
            {"book_id": book_id},
        )
        after = connection.execute(
            text(
                "select updated_at from projection_checkpoints where book_id = :book_id"
            ),
            {"book_id": book_id},
        ).scalar_one()
    assert after > before


def test_async_trigger_functions_are_security_invoker_and_not_directly_executable(
    migrated_postgres_database,
):
    runtime = migrated_postgres_database.runtime_role
    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("""
                select procedure.proname,
                       procedure.prosecdef,
                       procedure.proconfig,
                       exists (
                           select 1
                             from aclexplode(coalesce(procedure.proacl, acldefault('f', procedure.proowner))) acl
                            where acl.grantee = 0 and acl.privilege_type = 'EXECUTE'
                       ) as public_execute,
                       has_function_privilege(:runtime, procedure.oid, 'EXECUTE') as runtime_execute
                  from pg_catalog.pg_proc procedure
                  join pg_catalog.pg_namespace namespace on namespace.oid = procedure.pronamespace
                 where namespace.nspname = 'public'
                   and procedure.proname = any(:names)
                """),
                {"runtime": runtime, "names": sorted(TRIGGER_FUNCTIONS)},
            ).mappings()
            by_name = {row["proname"]: row for row in rows}
            assert set(by_name) == TRIGGER_FUNCTIONS
            for row in by_name.values():
                assert row["prosecdef"] is False
                assert "search_path=pg_catalog, public" in row["proconfig"]
                assert row["public_execute"] is False
                assert row["runtime_execute"] is False
    finally:
        engine.dispose()


def test_runtime_has_exact_async_projection_acl_and_only_dirty_delete(
    migrated_postgres_database,
):
    runtime = migrated_postgres_database.runtime_role
    engine = create_engine(migrated_postgres_database.runtime_url)
    try:
        with engine.connect() as connection:
            for table_name in ASYNC_TABLES:
                assert (
                    _column_privileges(connection, runtime, table_name, "INSERT")
                    == RUNTIME_INSERT_COLUMNS[table_name]
                )
                assert (
                    _column_privileges(connection, runtime, table_name, "UPDATE")
                    == RUNTIME_UPDATE_COLUMNS[table_name]
                )
                for privilege in ("TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"):
                    assert not connection.execute(
                        text(
                            "select has_table_privilege(:role, :table_name, :privilege)"
                        ),
                        {
                            "role": runtime,
                            "table_name": f"public.{table_name}",
                            "privilege": privilege,
                        },
                    ).scalar_one()
                expected_delete = table_name == "projection_dirty_periods"
                assert (
                    connection.execute(
                        text(
                            "select has_table_privilege(:role, :table_name, 'DELETE')"
                        ),
                        {"role": runtime, "table_name": f"public.{table_name}"},
                    ).scalar_one()
                    is expected_delete
                )
    finally:
        engine.dispose()
