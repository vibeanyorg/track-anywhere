from __future__ import annotations

from sqlalchemy import Numeric, inspect, text

from track_anywhere.infrastructure.db.base import V2Base, load_v2_models


LOT_COLUMNS = {
    "book_id",
    "lot_id",
    "acquisition_transaction_id",
    "instrument_asset_code",
    "settlement_asset_code",
    "acquired_quantity_units",
    "acquired_cost_units",
    "fee_units",
    "remaining_quantity_units",
    "remaining_cost_units",
    "source_event_id",
    "source_position",
}
ALLOCATION_COLUMNS = {
    "book_id",
    "allocation_id",
    "lot_id",
    "disposal_transaction_id",
    "allocation_position",
    "quantity_units",
    "cost_units",
    "source_event_id",
    "source_position",
}


def _constraint_definitions(connection, table_name: str) -> dict[str, str]:
    rows = connection.execute(
        text(
            """
            select constraint_record.conname,
                   pg_get_constraintdef(constraint_record.oid) as definition
              from pg_catalog.pg_constraint constraint_record
              join pg_catalog.pg_class relation
                on relation.oid = constraint_record.conrelid
              join pg_catalog.pg_namespace namespace
                on namespace.oid = relation.relnamespace
             where namespace.nspname = 'public'
               and relation.relname = :table_name
            """
        ),
        {"table_name": table_name},
    ).mappings()
    return {
        str(row["conname"]): " ".join(str(row["definition"]).lower().split())
        for row in rows
    }


def _column_privileges(
    connection,
    role: str,
    table_name: str,
    privilege: str,
) -> set[str]:
    return set(
        connection.scalars(
            text(
                """
                select column_name
                  from information_schema.role_column_grants
                 where grantee = :role
                   and table_schema = 'public'
                   and table_name = :table_name
                   and privilege_type = :privilege
                """
            ),
            {
                "role": role,
                "table_name": table_name,
                "privilege": privilege,
            },
        )
    )


def test_models_publish_the_exact_integer_lot_projection_contract() -> None:
    load_v2_models()

    lots = V2Base.metadata.tables["investment_lots"]
    allocations = V2Base.metadata.tables["investment_lot_allocations"]
    assert set(lots.c.keys()) == LOT_COLUMNS
    assert set(allocations.c.keys()) == ALLOCATION_COLUMNS
    for column in (
        lots.c.acquired_quantity_units,
        lots.c.acquired_cost_units,
        lots.c.fee_units,
        lots.c.remaining_quantity_units,
        lots.c.remaining_cost_units,
        allocations.c.quantity_units,
        allocations.c.cost_units,
    ):
        assert isinstance(column.type, Numeric)
        assert (column.type.precision, column.type.scale) == (38, 0)


def test_migration_creates_lot_tables_and_registers_sync_events(pg_engine) -> None:
    inspector = inspect(pg_engine)
    assert {"investment_lots", "investment_lot_allocations"} <= set(
        inspector.get_table_names()
    )
    assert {column["name"] for column in inspector.get_columns("investment_lots")} == (
        LOT_COLUMNS
    )
    assert {
        column["name"] for column in inspector.get_columns("investment_lot_allocations")
    } == ALLOCATION_COLUMNS

    with pg_engine.connect() as connection:
        registered = set(
            connection.execute(
                text(
                    """
                    select event_type, event_schema_version, projection_version
                      from synchronous_projection_event_types
                     where event_type like 'InvestmentLot%'
                    """
                )
            ).tuples()
        )
    assert registered == {
        ("InvestmentLotAcquired", 1, 1),
        ("InvestmentLotDisposed", 1, 1),
    }


def test_lot_sources_and_linked_transactions_are_book_scoped(pg_engine) -> None:
    with pg_engine.connect() as connection:
        lots = _constraint_definitions(connection, "investment_lots")
        allocations = _constraint_definitions(connection, "investment_lot_allocations")

    assert any(
        "foreign key (book_id, acquisition_transaction_id)" in definition
        and "references journal_transactions(book_id, transaction_id)" in definition
        for definition in lots.values()
    )
    assert any(
        "foreign key (book_id, disposal_transaction_id)" in definition
        and "references journal_transactions(book_id, transaction_id)" in definition
        for definition in allocations.values()
    )
    for constraints in (lots, allocations):
        assert any(
            "foreign key (book_id, source_event_id)" in definition
            and "references ledger_events(book_id, event_id)" in definition
            for definition in constraints.values()
        )
        assert any(
            "foreign key (book_id, source_position)" in definition
            and "references ledger_events(book_id, book_position)" in definition
            for definition in constraints.values()
        )

    assert any(
        "foreign key (book_id, lot_id)" in definition
        and "references investment_lots(book_id, lot_id)" in definition
        for definition in allocations.values()
    )


def test_source_pair_triggers_and_append_only_allocation_guard_exist(pg_engine) -> None:
    with pg_engine.connect() as connection:
        triggers = set(
            connection.scalars(
                text(
                    """
                    select trigger_record.tgname
                      from pg_catalog.pg_trigger trigger_record
                      join pg_catalog.pg_class relation
                        on relation.oid = trigger_record.tgrelid
                      join pg_catalog.pg_namespace namespace
                        on namespace.oid = relation.relnamespace
                     where namespace.nspname = 'public'
                       and relation.relname in (
                           'investment_lots', 'investment_lot_allocations'
                       )
                       and not trigger_record.tgisinternal
                    """
                )
            )
        )
    assert triggers == {
        "trg_investment_lots_source_projection",
        "trg_investment_lots_require_allocation",
        "trg_investment_lot_allocations_source_projection",
        "trg_investment_lot_allocations_immutable",
    }


def test_runtime_has_minimum_lot_projection_privileges(
    pg_engine,
    migrated_postgres_database,
) -> None:
    runtime = migrated_postgres_database.runtime_role
    with pg_engine.connect() as connection:
        for table_name in ("investment_lots", "investment_lot_allocations"):
            assert connection.execute(
                text("select has_table_privilege(:role, :table_name, 'SELECT')"),
                {"role": runtime, "table_name": f"public.{table_name}"},
            ).scalar_one()
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

        assert (
            _column_privileges(connection, runtime, "investment_lots", "INSERT")
            == LOT_COLUMNS
        )
        assert _column_privileges(connection, runtime, "investment_lots", "UPDATE") == {
            "remaining_quantity_units",
            "remaining_cost_units",
            "source_event_id",
            "source_position",
        }
        assert (
            _column_privileges(
                connection,
                runtime,
                "investment_lot_allocations",
                "INSERT",
            )
            == ALLOCATION_COLUMNS
        )
        assert not _column_privileges(
            connection,
            runtime,
            "investment_lot_allocations",
            "UPDATE",
        )
