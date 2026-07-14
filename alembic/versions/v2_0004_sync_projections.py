"""Add synchronous journal, balance, reversal, and reporting projections."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "v2_0004_sync_projections"
down_revision = "v2_0003_event_store"
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_POSTING_SIDE = postgresql.ENUM(
    "debit", "credit", name="posting_side", create_type=False
)
_TABLES = (
    "synchronous_projection_event_types",
    "journal_transactions",
    "journal_postings",
    "account_balances",
    "transaction_reversals",
    "transaction_external_references",
    "reporting_lines",
    "synchronous_projection_applied_events",
)
_INSERT_COLUMNS = {
    "journal_transactions": (
        "book_id",
        "transaction_id",
        "source_event_id",
        "source_position",
        "effective_at",
        "transaction_kind",
        "description_ref",
    ),
    "journal_postings": (
        "book_id",
        "transaction_id",
        "posting_id",
        "posting_position",
        "account_id",
        "asset_code",
        "side",
        "units",
    ),
    "account_balances": (
        "book_id",
        "account_id",
        "asset_code",
        "balance_units",
        "as_of_position",
    ),
    "transaction_reversals": (
        "book_id",
        "reversal_transaction_id",
        "original_transaction_id",
        "source_event_id",
        "original_event_id",
        "original_event_hash",
        "reason_code",
    ),
    "transaction_external_references": (
        "book_id",
        "transaction_id",
        "provider_code",
        "reference_kind",
        "reference_value",
        "source_event_id",
    ),
    "reporting_lines": (
        "book_id",
        "transaction_id",
        "classification_revision",
        "line_id",
        "line_version_id",
        "catalog_id",
        "line_position",
        "asset_code",
        "units",
        "line_kind",
        "dimension",
        "dimension_id",
        "description_ref",
        "source_event_id",
    ),
    "synchronous_projection_applied_events": (
        "book_id",
        "event_id",
        "projection_version",
    ),
}
_UPDATE_COLUMNS = {
    "account_balances": ("balance_units", "as_of_position"),
    "transaction_external_references": ("reference_value", "source_event_id"),
    "reporting_lines": (
        "classification_revision",
        "line_version_id",
        "catalog_id",
        "line_position",
        "asset_code",
        "units",
        "line_kind",
        "dimension",
        "dimension_id",
        "description_ref",
        "source_event_id",
    ),
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


def _create_tables() -> None:
    posting_side = postgresql.ENUM("debit", "credit", name="posting_side")
    posting_side.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "synchronous_projection_event_types",
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_schema_version", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name=op.f("ck_synchronous_projection_event_types_event_type_nonblank"),
        ),
        sa.CheckConstraint(
            "event_schema_version > 0",
            name=op.f("ck_synchronous_projection_event_types_schema_version_positive"),
        ),
        sa.PrimaryKeyConstraint(
            "event_type",
            "event_schema_version",
            name=op.f("pk_synchronous_projection_event_types"),
        ),
    )

    op.create_table(
        "journal_transactions",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.BigInteger(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transaction_kind", sa.String(length=32), nullable=False),
        sa.Column("description_ref", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "source_position > 0",
            name=op.f("ck_journal_transactions_source_position_positive"),
        ),
        sa.CheckConstraint(
            "transaction_kind in ('standard', 'opening', 'adjustment', "
            "'transfer', 'fx', 'investment_cash')",
            name=op.f("ck_journal_transactions_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_journal_transactions_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_journal_transactions_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "description_ref"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_journal_transactions_description",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id", "transaction_id", name=op.f("pk_journal_transactions")
        ),
        sa.UniqueConstraint(
            "book_id",
            "source_event_id",
            name="uq_journal_transactions_book_source_event",
        ),
        sa.UniqueConstraint(
            "book_id",
            "transaction_id",
            "source_event_id",
            name="uq_journal_transactions_transaction_source",
        ),
    )

    op.create_table(
        "journal_postings",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("posting_position", sa.SmallInteger(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("side", _POSTING_SIDE, nullable=False),
        sa.Column("units", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.CheckConstraint(
            "posting_position >= 0",
            name=op.f("ck_journal_postings_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "units > 0", name=op.f("ck_journal_postings_units_positive")
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_journal_postings_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_journal_postings_account_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id", "posting_id", name=op.f("pk_journal_postings")
        ),
        sa.UniqueConstraint(
            "book_id",
            "transaction_id",
            "posting_position",
            name="uq_journal_postings_transaction_position",
        ),
    )
    op.create_index(
        "ix_journal_postings_account_transaction",
        "journal_postings",
        ["book_id", "account_id", "transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_journal_postings_transaction_asset_side",
        "journal_postings",
        ["book_id", "transaction_id", "asset_code", "side"],
        unique=False,
    )

    op.create_table(
        "account_balances",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("balance_units", sa.Numeric(precision=48, scale=0), nullable=False),
        sa.Column("as_of_position", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "as_of_position > 0",
            name=op.f("ck_account_balances_as_of_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_account_balances_account_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "as_of_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_account_balances_as_of_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "account_id",
            "asset_code",
            name=op.f("pk_account_balances"),
        ),
    )

    op.create_table(
        "transaction_reversals",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("reversal_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("original_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("original_event_id", sa.Uuid(), nullable=False),
        sa.Column("original_event_hash", sa.LargeBinary(), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "reversal_transaction_id <> original_transaction_id",
            name=op.f("ck_transaction_reversals_distinct_transactions"),
        ),
        sa.CheckConstraint(
            "octet_length(original_event_hash) = 32",
            name=op.f("ck_transaction_reversals_original_hash_length"),
        ),
        sa.CheckConstraint(
            "reason_code in ('user_correction', 'duplicate', "
            "'import_correction', 'provider_reversal')",
            name=op.f("ck_transaction_reversals_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "reversal_transaction_id", "source_event_id"],
            [
                "journal_transactions.book_id",
                "journal_transactions.transaction_id",
                "journal_transactions.source_event_id",
            ],
            name="fk_transaction_reversals_reversal_source",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "original_transaction_id", "original_event_id"],
            [
                "journal_transactions.book_id",
                "journal_transactions.transaction_id",
                "journal_transactions.source_event_id",
            ],
            name="fk_transaction_reversals_original_source",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "reversal_transaction_id",
            name=op.f("pk_transaction_reversals"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "original_transaction_id",
            name="uq_transaction_reversals_original_target",
        ),
    )

    op.create_table(
        "transaction_external_references",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("reference_kind", sa.String(length=32), nullable=False),
        sa.Column("reference_value", sa.String(length=128), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name=op.f("ck_transaction_external_references_provider_valid"),
        ),
        sa.CheckConstraint(
            "reference_kind in ('provider_transaction', 'bank_transaction', "
            "'card_transaction', 'broker_trade')",
            name=op.f("ck_transaction_external_references_kind_valid"),
        ),
        sa.CheckConstraint(
            "reference_value ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
            name=op.f("ck_transaction_external_references_value_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_transaction_external_references_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_transaction_external_references_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "transaction_id",
            "provider_code",
            "reference_kind",
            name=op.f("pk_transaction_external_references"),
        ),
    )

    op.create_table(
        "reporting_lines",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("classification_revision", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("line_version_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("line_position", sa.SmallInteger(), nullable=False),
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("units", sa.Numeric(precision=38, scale=0), nullable=False),
        sa.Column("line_kind", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("dimension_id", sa.Uuid(), nullable=True),
        sa.Column("description_ref", sa.Uuid(), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "classification_revision > 0",
            name=op.f("ck_reporting_lines_revision_positive"),
        ),
        sa.CheckConstraint(
            "line_position >= 0",
            name=op.f("ck_reporting_lines_position_nonnegative"),
        ),
        sa.CheckConstraint("units > 0", name=op.f("ck_reporting_lines_units_positive")),
        sa.CheckConstraint(
            "line_kind in ('expense', 'income', 'transfer', 'tax', 'investment')",
            name=op.f("ck_reporting_lines_kind_valid"),
        ),
        sa.CheckConstraint(
            "dimension in ('category', 'project', 'counterparty', 'tax')",
            name=op.f("ck_reporting_lines_dimension_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_reporting_lines_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["asset_code"],
            ["assets.asset_code"],
            name="fk_reporting_lines_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_reporting_lines_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "description_ref"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_reporting_lines_description",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "transaction_id",
            "line_id",
            name=op.f("pk_reporting_lines"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "transaction_id",
            "classification_revision",
            "line_position",
            name="uq_reporting_lines_revision_position",
        ),
        sa.UniqueConstraint(
            "book_id",
            "transaction_id",
            "line_version_id",
            name="uq_reporting_lines_version",
        ),
    )

    op.create_table(
        "synchronous_projection_applied_events",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "projection_version > 0",
            name=op.f("ck_synchronous_projection_applied_events_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_synchronous_projection_applied_events_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "event_id",
            name=op.f("pk_synchronous_projection_applied_events"),
        ),
    )

    op.bulk_insert(
        sa.table(
            "synchronous_projection_event_types",
            sa.column("event_type", sa.String()),
            sa.column("event_schema_version", sa.SmallInteger()),
        ),
        [
            {"event_type": event_type, "event_schema_version": 1}
            for event_type in (
                "FinancialExternalReferenceCorrected",
                "JournalTransactionPosted",
                "JournalTransactionReversed",
                "ReportingLinesAssigned",
                "ReportingLinesCleared",
            )
        ],
    )


def _create_trigger_function(
    function_name: str, body: str, quoted_runtime: str
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
        declare
            target_book_id uuid;
            target_transaction_id uuid;
            posting_count bigint;
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


def _create_triggers(runtime_role: str) -> None:
    quoted_runtime = _quote_identifier(runtime_role)
    balance_validation = """
        if tg_table_name = 'journal_transactions' then
            target_book_id := new.book_id;
            target_transaction_id := new.transaction_id;
        elsif tg_op = 'INSERT' then
            target_book_id := new.book_id;
            target_transaction_id := new.transaction_id;
        else
            target_book_id := old.book_id;
            target_transaction_id := old.transaction_id;
        end if;

        if exists (
            select 1
              from public.journal_transactions transaction_record
             where transaction_record.book_id = target_book_id
               and transaction_record.transaction_id = target_transaction_id
        ) then
            select count(*)
              into posting_count
              from public.journal_postings posting
             where posting.book_id = target_book_id
               and posting.transaction_id = target_transaction_id;
            if posting_count < 2 then
                raise exception using errcode = '23514',
                    message = 'journal transaction requires at least two postings';
            end if;
            if exists (
                select 1
                  from public.journal_postings posting
                 where posting.book_id = target_book_id
                   and posting.transaction_id = target_transaction_id
                 group by posting.asset_code
                having sum(
                    case posting.side
                        when 'debit' then posting.units
                        else -posting.units
                    end
                ) <> 0
            ) then
                raise exception using errcode = '23514',
                    message = 'journal transaction must balance independently per asset';
            end if;
        end if;

        if tg_table_name = 'journal_postings'
           and tg_op = 'UPDATE'
           and (new.book_id, new.transaction_id)
               is distinct from (old.book_id, old.transaction_id) then
            target_book_id := new.book_id;
            target_transaction_id := new.transaction_id;
            if exists (
                select 1
                  from public.journal_transactions transaction_record
                 where transaction_record.book_id = target_book_id
                   and transaction_record.transaction_id = target_transaction_id
            ) then
                select count(*)
                  into posting_count
                  from public.journal_postings posting
                 where posting.book_id = target_book_id
                   and posting.transaction_id = target_transaction_id;
                if posting_count < 2 then
                    raise exception using errcode = '23514',
                        message = 'journal transaction requires at least two postings';
                end if;
                if exists (
                    select 1
                      from public.journal_postings posting
                     where posting.book_id = target_book_id
                       and posting.transaction_id = target_transaction_id
                     group by posting.asset_code
                    having sum(
                        case posting.side
                            when 'debit' then posting.units
                            else -posting.units
                        end
                    ) <> 0
                ) then
                    raise exception using errcode = '23514',
                        message = 'journal transaction must balance independently per asset';
                end if;
            end if;
        end if;
        return null;
    """
    functions = {
        "v2_validate_journal_balance": balance_validation,
        "v2_validate_journal_source_projection": """
            perform 1
              from public.ledger_events event_record
             where event_record.book_id = new.book_id
               and event_record.event_id = new.source_event_id
               and event_record.book_position = new.source_position
               and event_record.stream_id = new.transaction_id
               and event_record.event_type in (
                   'JournalTransactionPosted', 'JournalTransactionReversed'
               );
            if not found then
                raise exception using errcode = '23514',
                    message = 'journal transaction must bind its exact source event';
            end if;
            return new;
        """,
        "v2_require_sync_projection": """
            if exists (
                select 1
                  from public.synchronous_projection_event_types required
                 where required.event_type = new.event_type
                   and required.event_schema_version = new.event_schema_version
            ) and not exists (
                select 1
                  from public.synchronous_projection_applied_events applied
                 where applied.book_id = new.book_id
                   and applied.event_id = new.event_id
            ) then
                raise exception using errcode = '23514',
                    message = 'sync-required event must have an applied projection marker';
            end if;
            return null;
        """,
        "v2_validate_sync_projection_marker": """
            perform 1
              from public.ledger_events event_record
              join public.synchronous_projection_event_types required
                on required.event_type = event_record.event_type
               and required.event_schema_version = event_record.event_schema_version
             where event_record.book_id = new.book_id
               and event_record.event_id = new.event_id;
            if not found then
                raise exception using errcode = '23514',
                    message = 'projection marker requires a registered sync event';
            end if;
            return new;
        """,
        "v2_reject_projection_marker_mutation": """
            raise exception using errcode = '23514',
                message = 'synchronous projection markers are append-only';
        """,
    }
    for function_name, body in functions.items():
        _create_trigger_function(function_name, body, quoted_runtime)

    connection = op.get_bind()
    connection.exec_driver_sql(
        "create trigger trg_journal_transactions_source_projection "
        "before insert or update on public.journal_transactions "
        "for each row execute function "
        "public.v2_validate_journal_source_projection()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_journal_transactions_balanced_commit "
        "after insert on public.journal_transactions "
        "deferrable initially deferred for each row execute function "
        "public.v2_validate_journal_balance()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_journal_postings_balanced_commit "
        "after insert or update or delete on public.journal_postings "
        "deferrable initially deferred for each row execute function "
        "public.v2_validate_journal_balance()"
    )
    connection.exec_driver_sql(
        "create constraint trigger trg_ledger_events_sync_projection_commit "
        "after insert on public.ledger_events "
        "deferrable initially deferred for each row execute function "
        "public.v2_require_sync_projection()"
    )
    connection.exec_driver_sql(
        "create trigger trg_sync_projection_markers_validate "
        "before insert on public.synchronous_projection_applied_events "
        "for each row execute function "
        "public.v2_validate_sync_projection_marker()"
    )
    connection.exec_driver_sql(
        "create trigger trg_sync_projection_markers_immutable "
        "before update or delete on public.synchronous_projection_applied_events "
        "for each row execute function "
        "public.v2_reject_projection_marker_mutation()"
    )


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


def _apply_runtime_acl(runtime_role: str) -> None:
    connection = op.get_bind()
    quoted_runtime = _quote_identifier(runtime_role)
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

    connection.exec_driver_sql(
        "revoke all privileges on type public.posting_side "
        f"from public, {quoted_runtime}"
    )
    connection.exec_driver_sql(
        f"grant usage on type public.posting_side to {quoted_runtime}"
    )


def upgrade() -> None:
    runtime_role = _runtime_role()
    _create_tables()
    _create_triggers(runtime_role)
    _apply_runtime_acl(runtime_role)


def downgrade() -> None:
    raise RuntimeError(
        "the Track Anywhere V2 sync-projection migration is irreversible"
    )
