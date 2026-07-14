from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


EVENT_INSERT_COLUMNS = {
    "actor_subject_id",
    "book_id",
    "book_position",
    "causation_event_id",
    "command_id",
    "correlation_id",
    "effective_at",
    "event_hash",
    "event_id",
    "event_schema_version",
    "event_type",
    "payload",
    "previous_hash",
    "stream_id",
    "stream_type",
    "stream_version",
}
HEAD_PRIVILEGES = {
    "book_event_heads": {
        "insert": {"book_id", "last_hash", "last_position"},
        "update": {"last_hash", "last_position"},
    },
    "event_stream_heads": {
        "insert": {
            "book_id",
            "last_book_position",
            "last_event_id",
            "last_version",
            "stream_id",
            "stream_type",
        },
        "update": {"last_book_position", "last_event_id", "last_version"},
    },
    "command_receipts": {
        "insert": {
            "actor_subject_id",
            "book_id",
            "command_id",
            "idempotency_key_hash",
            "operation",
            "request_hash",
            "status",
        },
        "update": {
            "completed_at",
            "first_book_position",
            "last_book_position",
            "response_schema_version",
            "result_body",
            "result_status",
            "status",
        },
    },
}


def _insert_book_and_event(pg_engine):
    book_id = uuid4()
    event_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
                """
            )
        )
        connection.execute(
            text(
                """
                insert into books (
                    book_id, current_name, base_asset_code, write_state
                ) values (:book_id, 'Permission book', 'USD', 'active')
                """
            ),
            {"book_id": book_id},
        )
        connection.execute(
            text(
                """
                insert into ledger_events (
                    event_id, book_id, book_position, stream_type, stream_id,
                    stream_version, event_type, event_schema_version, command_id,
                    actor_subject_id, correlation_id, causation_event_id,
                    effective_at, payload, previous_hash, event_hash
                ) values (
                    :event_id, :book_id, 1, 'investment_lot', :stream_id,
                    1, 'InvestmentLotAcquired', 1, :command_id,
                    'human:test-user', :correlation_id, null, :effective_at,
                    '{}'::jsonb, :previous_hash, :event_hash
                )
                """
            ),
            {
                "event_id": event_id,
                "book_id": book_id,
                "stream_id": uuid4(),
                "command_id": uuid4(),
                "correlation_id": uuid4(),
                "effective_at": datetime.now(UTC),
                "previous_hash": b"p" * 32,
                "event_hash": b"e" * 32,
            },
        )
    return book_id, event_id


def _column_privileges(connection, role: str, table_name: str, privilege: str):
    return {
        column
        for column in connection.execute(
            text(
                """
                select column_record.attname
                  from pg_catalog.pg_attribute column_record
                  join pg_catalog.pg_class relation
                    on relation.oid = column_record.attrelid
                  join pg_catalog.pg_namespace namespace
                    on namespace.oid = relation.relnamespace
                 where namespace.nspname = 'public'
                   and relation.relname = :table_name
                   and column_record.attnum > 0
                   and not column_record.attisdropped
                   and has_column_privilege(
                       :role,
                       format('%I.%I', namespace.nspname, relation.relname),
                       column_record.attname,
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


def test_runtime_has_only_minimum_event_store_columns_and_object_privileges(
    migrated_postgres_database,
) -> None:
    runtime = migrated_postgres_database.runtime_role
    with create_engine(migrated_postgres_database.runtime_url).connect() as connection:
        event_table = {
            privilege: connection.execute(
                text(
                    "select has_table_privilege(:role, 'public.ledger_events', :privilege)"
                ),
                {"role": runtime, "privilege": privilege},
            ).scalar_one()
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
                "MAINTAIN",
            )
        }
        assert event_table == {
            "SELECT": True,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
            "MAINTAIN": False,
        }
        assert (
            _column_privileges(connection, runtime, "ledger_events", "INSERT")
            == EVENT_INSERT_COLUMNS
        )
        assert (
            _column_privileges(connection, runtime, "ledger_events", "UPDATE") == set()
        )

        for table_name, expected in HEAD_PRIVILEGES.items():
            assert connection.execute(
                text("select has_table_privilege(:role, :table_name, 'SELECT')"),
                {"role": runtime, "table_name": f"public.{table_name}"},
            ).scalar_one()
            assert (
                _column_privileges(connection, runtime, table_name, "INSERT")
                == expected["insert"]
            )
            assert (
                _column_privileges(connection, runtime, table_name, "UPDATE")
                == expected["update"]
            )
            for privilege in (
                "DELETE",
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

        sequence_privileges = {
            privilege: connection.execute(
                text(
                    "select has_sequence_privilege("
                    ":role, 'public.ledger_global_sequence', :privilege)"
                ),
                {"role": runtime, "privilege": privilege},
            ).scalar_one()
            for privilege in ("USAGE", "SELECT", "UPDATE")
        }
        assert sequence_privileges == {
            "USAGE": True,
            "SELECT": False,
            "UPDATE": False,
        }
        sequence_shape = connection.execute(
            text(
                """
                select sequence_record.seqtypid = 'bigint'::regtype,
                       sequence_record.seqcycle,
                       pg_get_serial_sequence(
                           'public.ledger_events', 'global_sequence'
                       )
                  from pg_catalog.pg_sequence sequence_record
                 where sequence_record.seqrelid =
                       'public.ledger_global_sequence'::regclass
                """
            )
        ).one()
        assert tuple(sequence_shape) == (
            True,
            False,
            "public.ledger_global_sequence",
        )
        sequence_acl = {
            (
                grantee if grantee is not None else "PUBLIC",
                privilege,
                grantable,
            )
            for grantee, privilege, grantable in connection.execute(
                text(
                    """
                    select grantee.rolname, acl.privilege_type, acl.is_grantable
                      from pg_catalog.pg_class sequence_record
                      cross join lateral pg_catalog.aclexplode(
                          coalesce(
                              sequence_record.relacl,
                              pg_catalog.acldefault(
                                  'S', sequence_record.relowner
                              )
                          )
                      ) acl
                      left join pg_catalog.pg_roles grantee
                        on grantee.oid = acl.grantee
                     where sequence_record.oid =
                           'public.ledger_global_sequence'::regclass
                       and (acl.grantee = 0 or grantee.rolname = :runtime)
                    """
                ),
                {"runtime": runtime},
            )
        }
        assert sequence_acl == {(runtime, "USAGE", False)}
        assert connection.execute(
            text("select has_type_privilege(:role, 'public.receipt_status', 'USAGE')"),
            {"role": runtime},
        ).scalar_one()
        assert not connection.execute(
            text(
                "select has_type_privilege('public', 'public.receipt_status', 'USAGE')"
            )
        ).scalar_one()
        type_acl = {
            (
                grantee if grantee is not None else "PUBLIC",
                privilege,
                grantable,
            )
            for grantee, privilege, grantable in connection.execute(
                text(
                    """
                    select grantee.rolname, acl.privilege_type, acl.is_grantable
                      from pg_catalog.pg_type type_record
                      cross join lateral pg_catalog.aclexplode(
                          coalesce(
                              type_record.typacl,
                              pg_catalog.acldefault('T', type_record.typowner)
                          )
                      ) acl
                      left join pg_catalog.pg_roles grantee
                        on grantee.oid = acl.grantee
                     where type_record.oid = 'public.receipt_status'::regtype
                       and (acl.grantee = 0 or grantee.rolname = :runtime)
                    """
                ),
                {"runtime": runtime},
            )
        }
        assert type_acl == {(runtime, "USAGE", False)}


def test_runtime_cannot_supply_generated_event_fields_or_mutate_event_store(
    pg_engine, migrated_postgres_database
) -> None:
    book_id, event_id = _insert_book_and_event(pg_engine)
    forbidden = (
        (
            "update ledger_events set event_type = 'Changed' "
            "where event_id = :event_id",
            {"event_id": event_id},
        ),
        (
            "delete from ledger_events where event_id = :event_id",
            {"event_id": event_id},
        ),
        ("truncate ledger_events", {}),
        ("alter table ledger_events disable trigger all", {}),
        ("alter table ledger_events add column forbidden integer", {}),
        (
            "select setval('public.ledger_global_sequence', 999999, true)",
            {},
        ),
        (
            """
            insert into ledger_events (
                event_id, global_sequence, book_id, book_position,
                stream_type, stream_id, stream_version, event_type,
                event_schema_version, command_id, actor_subject_id,
                correlation_id, effective_at, recorded_at, payload,
                previous_hash, event_hash
            ) values (
                :event_id, 999999, :book_id, 2, 'investment_lot',
                :stream_id, 1, 'InvestmentLotAcquired', 1, :command_id,
                'human:test-user', :correlation_id, :effective_at, :recorded_at,
                '{}'::jsonb, :previous_hash, :event_hash
            )
            """,
            {
                "event_id": uuid4(),
                "book_id": book_id,
                "stream_id": uuid4(),
                "command_id": uuid4(),
                "correlation_id": uuid4(),
                "effective_at": datetime.now(UTC),
                "recorded_at": datetime.now(UTC),
                "previous_hash": b"p" * 32,
                "event_hash": b"f" * 32,
            },
        ),
    )
    for statement, parameters in forbidden:
        with pytest.raises(DBAPIError):
            with pg_engine.begin() as connection:
                connection.execute(text(statement), parameters)

    with create_engine(migrated_postgres_database.admin_url).connect() as connection:
        assert not connection.execute(
            text(
                """
                select relation.relowner = role.oid
                  from pg_catalog.pg_class relation
                  join pg_catalog.pg_roles role on role.rolname = :runtime
                 where relation.oid = 'public.ledger_events'::regclass
                """
            ),
            {"runtime": migrated_postgres_database.runtime_role},
        ).scalar_one()


def test_ledger_rows_are_immutable_for_owner_but_migrator_can_run_ddl(
    pg_engine, migrated_postgres_database
) -> None:
    _, event_id = _insert_book_and_event(pg_engine)
    owner_engine = create_engine(migrated_postgres_database.migrator_url)
    try:
        for statement in (
            "update ledger_events set event_type = 'Changed' "
            "where event_id = :event_id",
            "delete from ledger_events where event_id = :event_id",
        ):
            with pytest.raises(DBAPIError):
                with owner_engine.begin() as connection:
                    connection.execute(
                        text(f'SET ROLE "{migrated_postgres_database.owner_role}"')
                    )
                    connection.execute(text(statement), {"event_id": event_id})

        with owner_engine.begin() as connection:
            connection.execute(
                text(f'SET ROLE "{migrated_postgres_database.owner_role}"')
            )
            connection.execute(text("create table task8_migration_probe (id integer)"))
            connection.execute(text("drop table task8_migration_probe"))
    finally:
        owner_engine.dispose()


def test_event_store_trigger_functions_are_invoker_only_and_private(
    pg_engine, migrated_postgres_database
) -> None:
    tables = (
        "'book_event_heads', 'event_stream_heads', 'ledger_events', 'command_receipts'"
    )
    with pg_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    f"""
                    select relation.relname as table_name,
                           function_record.proname as function_name,
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
                                 ) function_acl
                                 left join pg_catalog.pg_roles grantee
                                   on grantee.oid = function_acl.grantee
                                where function_acl.privilege_type = 'EXECUTE'
                                  and (
                                      function_acl.grantee = 0
                                      or grantee.rolname = :runtime
                                  )
                           ) as broadly_executable
                      from pg_catalog.pg_trigger trigger_record
                      join pg_catalog.pg_class relation
                        on relation.oid = trigger_record.tgrelid
                      join pg_catalog.pg_proc function_record
                        on function_record.oid = trigger_record.tgfoid
                     where not trigger_record.tgisinternal
                       and relation.relname in ({tables})
                     order by relation.relname, function_record.proname
                    """
                ),
                {"runtime": migrated_postgres_database.runtime_role},
            )
            .mappings()
            .all()
        )

    assert {row["table_name"] for row in rows} == {
        "book_event_heads",
        "command_receipts",
        "event_stream_heads",
        "ledger_events",
    }
    assert len(rows) == 6
    assert any(row["function_name"] == "v2_require_sync_projection" for row in rows)
    for row in rows:
        assert row["prosecdef"] is False
        assert row["proconfig"] is not None
        assert any(
            setting.replace(" ", "") == "search_path=pg_catalog,public"
            for setting in row["proconfig"]
        )
        assert row["broadly_executable"] is False
