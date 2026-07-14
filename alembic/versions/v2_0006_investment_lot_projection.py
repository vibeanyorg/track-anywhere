"""Add synchronous investment lot projections."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0006_investment_lots"
down_revision = "v2_0005_async_projection_outbox"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_TABLES = ("investment_lots", "investment_lot_allocations")
_INSERT_COLUMNS = {
    "investment_lots": (
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
    ),
    "investment_lot_allocations": (
        "book_id",
        "allocation_id",
        "lot_id",
        "disposal_transaction_id",
        "allocation_position",
        "quantity_units",
        "cost_units",
        "source_event_id",
        "source_position",
    ),
}
_UPDATE_COLUMNS = {
    "investment_lots": (
        "remaining_quantity_units",
        "remaining_cost_units",
        "source_event_id",
        "source_position",
    )
}


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier) or len(identifier.encode("ascii")) > 63:
        raise RuntimeError(
            "database runtime role must be a safe lowercase PostgreSQL identifier"
        )
    return f'"{identifier}"'


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not value:
        raise RuntimeError("TRACK_ANYWHERE_DB_RUNTIME_ROLE is required")
    _quote_identifier(value)
    return value


def _grant_columns(
    quoted_runtime: str,
    table_name: str,
    privilege: str,
    columns: tuple[str, ...],
) -> None:
    op.get_bind().exec_driver_sql(
        f"grant {privilege} ({', '.join(columns)}) "
        f"on table public.{table_name} to {quoted_runtime}"
    )


def _create_trigger_function(
    function_name: str,
    body: str,
    quoted_runtime: str,
) -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        f"""
        create function public.{function_name}()
        returns trigger
        language plpgsql
        security invoker
        set search_path = pg_catalog, public
        as $function$
        begin
            {body}
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        f"revoke all privileges on function public.{function_name}() "
        f"from public, {quoted_runtime}"
    )


def _create_tables() -> None:
    op.create_table(
        "investment_lots",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("acquisition_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_asset_code", sa.String(length=16), nullable=False),
        sa.Column("settlement_asset_code", sa.String(length=16), nullable=False),
        sa.Column(
            "acquired_quantity_units", sa.Numeric(precision=38, scale=0), nullable=False
        ),
        sa.Column(
            "acquired_cost_units", sa.Numeric(precision=38, scale=0), nullable=False
        ),
        sa.Column("fee_units", sa.Numeric(precision=38, scale=0), nullable=True),
        sa.Column(
            "remaining_quantity_units",
            sa.Numeric(precision=38, scale=0),
            nullable=False,
        ),
        sa.Column(
            "remaining_cost_units",
            sa.Numeric(precision=38, scale=0),
            nullable=False,
        ),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "acquired_quantity_units > 0",
            name=op.f("ck_investment_lots_acquired_quantity_positive"),
        ),
        sa.CheckConstraint(
            "acquired_cost_units > 0",
            name=op.f("ck_investment_lots_acquired_cost_positive"),
        ),
        sa.CheckConstraint(
            "fee_units is null or fee_units > 0",
            name=op.f("ck_investment_lots_fee_positive"),
        ),
        sa.CheckConstraint(
            "remaining_quantity_units between 0 and acquired_quantity_units",
            name=op.f("ck_investment_lots_remaining_quantity_bounded"),
        ),
        sa.CheckConstraint(
            "remaining_cost_units between 0 and acquired_cost_units",
            name=op.f("ck_investment_lots_remaining_cost_bounded"),
        ),
        sa.CheckConstraint(
            "(remaining_quantity_units = 0 and remaining_cost_units = 0) or "
            "(remaining_quantity_units > 0 and remaining_cost_units > 0)",
            name=op.f("ck_investment_lots_remaining_state_complete"),
        ),
        sa.CheckConstraint(
            "source_position > 0",
            name=op.f("ck_investment_lots_source_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "acquisition_transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_investment_lots_acquisition_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_asset_code"],
            ["assets.asset_code"],
            name="fk_investment_lots_instrument_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_asset_code"],
            ["assets.asset_code"],
            name="fk_investment_lots_settlement_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_investment_lots_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_investment_lots_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("book_id", "lot_id", name=op.f("pk_investment_lots")),
    )
    op.create_index(
        "ix_investment_lots_open_pool",
        "investment_lots",
        (
            "book_id",
            "instrument_asset_code",
            "settlement_asset_code",
            "source_position",
            "lot_id",
        ),
        unique=False,
        postgresql_where=sa.text("remaining_quantity_units > 0"),
    )

    op.create_table(
        "investment_lot_allocations",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("disposal_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_position", sa.SmallInteger(), nullable=False),
        sa.Column("quantity_units", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.Column("cost_units", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "allocation_position >= 0",
            name=op.f("ck_investment_lot_allocations_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "quantity_units > 0",
            name=op.f("ck_investment_lot_allocations_quantity_positive"),
        ),
        sa.CheckConstraint(
            "cost_units > 0",
            name=op.f("ck_investment_lot_allocations_cost_positive"),
        ),
        sa.CheckConstraint(
            "source_position > 0",
            name=op.f("ck_investment_lot_allocations_source_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "lot_id"],
            ["investment_lots.book_id", "investment_lots.lot_id"],
            name="fk_investment_lot_allocations_lot",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "disposal_transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_investment_lot_allocations_disposal_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_investment_lot_allocations_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_investment_lot_allocations_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "allocation_id",
            name=op.f("pk_investment_lot_allocations"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "disposal_transaction_id",
            "allocation_position",
            name="uq_investment_lot_allocations_disposal_position",
        ),
        sa.UniqueConstraint(
            "book_id",
            "disposal_transaction_id",
            "lot_id",
            name="uq_investment_lot_allocations_disposal_lot",
        ),
    )
    op.create_index(
        "ix_investment_lot_allocations_disposal",
        "investment_lot_allocations",
        ("book_id", "disposal_transaction_id", "allocation_position"),
        unique=False,
    )


def _register_synchronous_events() -> None:
    op.bulk_insert(
        sa.table(
            "synchronous_projection_event_types",
            sa.column("event_type", sa.String()),
            sa.column("event_schema_version", sa.SmallInteger()),
            sa.column("projection_version", sa.Integer()),
        ),
        [
            {
                "event_type": event_type,
                "event_schema_version": 1,
                "projection_version": 1,
            }
            for event_type in ("InvestmentLotAcquired", "InvestmentLotDisposed")
        ],
    )


def _create_triggers(quoted_runtime: str) -> None:
    _create_trigger_function(
        "v2_validate_investment_lot_source_projection",
        """
        perform 1
          from public.ledger_events event_record
          join public.synchronous_projection_event_types required
            on required.event_type = event_record.event_type
           and required.event_schema_version = event_record.event_schema_version
         where event_record.book_id = new.book_id
           and event_record.event_id = new.source_event_id
           and event_record.book_position = new.source_position
           and (
               (
                   tg_op = 'INSERT'
                   and event_record.event_type = 'InvestmentLotAcquired'
                   and event_record.stream_type = 'investment_lot'
                   and event_record.stream_id = new.lot_id
               )
               or (
                   tg_op = 'UPDATE'
                   and event_record.event_type = 'InvestmentLotDisposed'
                   and event_record.stream_type = 'investment_disposal'
                   and event_record.stream_id =
                       (event_record.payload ->> 'transaction_id')::uuid
               )
           );
        if not found then
            raise exception using errcode = '23514',
                message = 'investment lot must bind its exact typed source event';
        end if;

        perform 1
          from public.journal_transactions transaction_record
         where transaction_record.book_id = new.book_id
           and transaction_record.transaction_id = new.acquisition_transaction_id
           and transaction_record.source_position < new.source_position;
        if not found then
            raise exception using errcode = '23514',
                message = 'investment lot acquisition transaction must precede its source event';
        end if;

        if tg_op = 'UPDATE' then
            if row(
                new.book_id,
                new.lot_id,
                new.acquisition_transaction_id,
                new.instrument_asset_code,
                new.settlement_asset_code,
                new.acquired_quantity_units,
                new.acquired_cost_units,
                new.fee_units
            ) is distinct from row(
                old.book_id,
                old.lot_id,
                old.acquisition_transaction_id,
                old.instrument_asset_code,
                old.settlement_asset_code,
                old.acquired_quantity_units,
                old.acquired_cost_units,
                old.fee_units
            ) then
                raise exception using errcode = '23514',
                    message = 'investment lot acquisition facts are immutable';
            end if;
            if new.remaining_quantity_units >= old.remaining_quantity_units
               or new.remaining_cost_units >= old.remaining_cost_units then
                raise exception using errcode = '23514',
                    message = 'investment lot disposal must decrease quantity and cost';
            end if;
            if new.source_position <= old.source_position then
                raise exception using errcode = '23514',
                    message = 'investment lot source position must advance';
            end if;
        end if;
        return new;
        """,
        quoted_runtime,
    )
    _create_trigger_function(
        "v2_validate_investment_lot_allocation_source_projection",
        """
        perform 1
          from public.ledger_events event_record
          join public.synchronous_projection_event_types required
            on required.event_type = event_record.event_type
           and required.event_schema_version = event_record.event_schema_version
         where event_record.book_id = new.book_id
           and event_record.event_id = new.source_event_id
           and event_record.book_position = new.source_position
           and event_record.event_type = 'InvestmentLotDisposed'
           and event_record.stream_type = 'investment_disposal'
           and event_record.stream_id = new.disposal_transaction_id
           and (event_record.payload ->> 'transaction_id')::uuid =
               new.disposal_transaction_id
           and exists (
               select 1
                 from pg_catalog.jsonb_array_elements(
                          event_record.payload -> 'allocations'
                      ) allocation
                where (allocation ->> 'allocation_id')::uuid = new.allocation_id
                  and (allocation ->> 'lot_id')::uuid = new.lot_id
                  and (allocation ->> 'position')::smallint =
                      new.allocation_position
                  and (allocation ->> 'quantity_units')::numeric =
                      new.quantity_units
                  and (allocation ->> 'cost_units')::numeric = new.cost_units
           );
        if not found then
            raise exception using errcode = '23514',
                message = 'lot allocation must match its exact disposal event fact';
        end if;

        perform 1
          from public.journal_transactions transaction_record
         where transaction_record.book_id = new.book_id
           and transaction_record.transaction_id = new.disposal_transaction_id
           and transaction_record.source_position < new.source_position;
        if not found then
            raise exception using errcode = '23514',
                message = 'lot disposal transaction must precede its source event';
        end if;
        return new;
        """,
        quoted_runtime,
    )
    _create_trigger_function(
        "v2_require_investment_lot_allocation_projection",
        """
        perform 1
          from public.investment_lot_allocations allocation
         where allocation.book_id = new.book_id
           and allocation.lot_id = new.lot_id
           and allocation.source_event_id = new.source_event_id
           and allocation.source_position = new.source_position
           and allocation.quantity_units =
               old.remaining_quantity_units - new.remaining_quantity_units
           and allocation.cost_units =
               old.remaining_cost_units - new.remaining_cost_units;
        if not found then
            raise exception using errcode = '23514',
                message = 'lot decrement requires an exact immutable allocation';
        end if;
        return new;
        """,
        quoted_runtime,
    )
    _create_trigger_function(
        "v2_reject_investment_lot_allocation_mutation",
        """
        raise exception using errcode = '23514',
            message = 'investment lot allocations are append-only';
        """,
        quoted_runtime,
    )

    connection = op.get_bind()
    connection.exec_driver_sql(
        "create trigger trg_investment_lots_source_projection "
        "before insert or update on public.investment_lots "
        "for each row execute function "
        "public.v2_validate_investment_lot_source_projection()"
    )
    connection.exec_driver_sql(
        "create trigger trg_investment_lot_allocations_source_projection "
        "before insert on public.investment_lot_allocations "
        "for each row execute function "
        "public.v2_validate_investment_lot_allocation_source_projection()"
    )
    connection.exec_driver_sql(
        "create trigger trg_investment_lot_allocations_immutable "
        "before update or delete on public.investment_lot_allocations "
        "for each row execute function "
        "public.v2_reject_investment_lot_allocation_mutation()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_investment_lots_require_allocation "
        "after update on public.investment_lots "
        "deferrable initially deferred for each row execute function "
        "public.v2_require_investment_lot_allocation_projection()"
    )


def _apply_runtime_acl(quoted_runtime: str) -> None:
    connection = op.get_bind()
    for table_name in _TABLES:
        connection.exec_driver_sql(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {quoted_runtime}"
        )
        connection.exec_driver_sql(
            f"grant select on table public.{table_name} to {quoted_runtime}"
        )
    for table_name, columns in _INSERT_COLUMNS.items():
        _grant_columns(quoted_runtime, table_name, "insert", columns)
    for table_name, columns in _UPDATE_COLUMNS.items():
        _grant_columns(quoted_runtime, table_name, "update", columns)


def upgrade() -> None:
    quoted_runtime = _quote_identifier(_runtime_role())
    _create_tables()
    _register_synchronous_events()
    _create_triggers(quoted_runtime)
    _apply_runtime_acl(quoted_runtime)


def downgrade() -> None:
    raise RuntimeError("the Track Anywhere V2 investment lot migration is irreversible")
