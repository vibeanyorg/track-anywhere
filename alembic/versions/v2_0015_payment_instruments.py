"""Add generic payment instruments and account bindings."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0015_payment_instruments"
down_revision = "v2_0014_everyday_entry_gateway"
branch_labels = None
depends_on = None


_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not _IDENTIFIER.fullmatch(value) or len(value.encode("ascii")) > 63:
        raise RuntimeError(
            "TRACK_ANYWHERE_DB_RUNTIME_ROLE is required and must be safe"
        )
    return f'"{value}"'


def upgrade() -> None:
    runtime = _runtime_role()
    op.create_table(
        "payment_instruments",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column(
            "instrument_kind",
            sa.String(length=16),
            server_default=sa.text("'card'"),
            nullable=False,
        ),
        sa.Column("form_factor", sa.String(length=16), nullable=False),
        sa.Column("network", sa.String(length=16), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("settlement_policy", sa.String(length=16), nullable=False),
        sa.Column("current_name", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "instrument_kind = 'card'",
            name=op.f("ck_payment_instruments_kind_valid"),
        ),
        sa.CheckConstraint(
            "form_factor in ('virtual','physical','single_use')",
            name=op.f("ck_payment_instruments_form_factor_valid"),
        ),
        sa.CheckConstraint(
            "network in ('mastercard','visa','amex','unionpay','other')",
            name=op.f("ck_payment_instruments_network_valid"),
        ),
        sa.CheckConstraint(
            "settlement_policy in ('immediate','prepaid','statement')",
            name=op.f("ck_payment_instruments_settlement_policy_valid"),
        ),
        sa.CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name=op.f("ck_payment_instruments_provider_code_valid"),
        ),
        sa.CheckConstraint(
            "btrim(current_name) <> ''",
            name=op.f("ck_payment_instruments_current_name_nonblank"),
        ),
        sa.CheckConstraint(
            "last4 is null or last4 ~ '^[0-9]{4}$'",
            name=op.f("ck_payment_instruments_last4_valid"),
        ),
        sa.CheckConstraint(
            "status in ('active','frozen','closed')",
            name=op.f("ck_payment_instruments_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id", "instrument_id", name=op.f("pk_payment_instruments")
        ),
    )
    op.create_index(
        "ix_payment_instruments_book_name",
        "payment_instruments",
        ["book_id", "current_name"],
    )
    op.create_table(
        "payment_instrument_bindings",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(length=16), nullable=False),
        sa.Column("binding_role", sa.String(length=32), nullable=False),
        sa.Column(
            "priority", sa.Integer(), server_default=sa.text("100"), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "binding_role in ('funding_asset','card_liability')",
            name=op.f("ck_payment_instrument_bindings_role_valid"),
        ),
        sa.CheckConstraint(
            "priority > 0",
            name=op.f("ck_payment_instrument_bindings_priority_positive"),
        ),
        sa.CheckConstraint(
            "status in ('active','closed')",
            name=op.f("ck_payment_instrument_bindings_status_valid"),
        ),
        sa.CheckConstraint(
            "effective_to is null or effective_to > effective_from",
            name=op.f("ck_payment_instrument_bindings_effective_window_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "instrument_id"],
            ["payment_instruments.book_id", "payment_instruments.instrument_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "binding_id",
            name=op.f("pk_payment_instrument_bindings"),
        ),
        sa.UniqueConstraint(
            "book_id",
            "binding_id",
            "instrument_id",
            name="uq_payment_instrument_bindings_instrument",
        ),
    )
    op.create_index(
        "ix_payment_instrument_bindings_resolution",
        "payment_instrument_bindings",
        [
            "book_id",
            "instrument_id",
            "asset_code",
            "status",
            "priority",
        ],
    )
    op.create_table(
        "payment_instrument_transactions",
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "instrument_id"],
            ["payment_instruments.book_id", "payment_instruments.instrument_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "binding_id", "instrument_id"],
            [
                "payment_instrument_bindings.book_id",
                "payment_instrument_bindings.binding_id",
                "payment_instrument_bindings.instrument_id",
            ],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "book_id",
            "transaction_id",
            name=op.f("pk_payment_instrument_transactions"),
        ),
    )
    op.execute(
        """
        create function public.v2_validate_payment_instrument_binding()
        returns trigger language plpgsql security invoker
        set search_path = pg_catalog, public as $function$
        declare
            instrument_policy varchar(16);
            instrument_status varchar(16);
            bound_account_type varchar(32);
            bound_account_subtype varchar(64);
            bound_account_role varchar(32);
            bound_account_status varchar(16);
        begin
            select instrument.settlement_policy, instrument.status
              into instrument_policy, instrument_status
              from public.payment_instruments instrument
             where instrument.book_id = new.book_id
               and instrument.instrument_id = new.instrument_id;
            select account.account_type, account.account_subtype,
                   account.system_role, account.status
              into bound_account_type, bound_account_subtype,
                   bound_account_role, bound_account_status
              from public.accounts account
             where account.book_id = new.book_id
               and account.account_id = new.account_id
               and account.asset_code = new.asset_code;
            if not found
               or bound_account_role is not null
               or (
                   new.status = 'active'
                   and (
                       instrument_status <> 'active'
                       or bound_account_status <> 'active'
                   )
               )
               or (
                   instrument_policy in ('immediate', 'prepaid')
                   and (
                       new.binding_role <> 'funding_asset'
                       or bound_account_type <> 'asset'
                   )
               )
               or (
                   instrument_policy = 'statement'
                   and (
                       new.binding_role <> 'card_liability'
                       or bound_account_type <> 'liability'
                       or bound_account_subtype <> 'credit_card'
                   )
               ) then
                raise exception using errcode = '23514',
                    message = 'payment instrument binding is invalid';
            end if;
            return new;
        end;
        $function$
        """
    )
    op.execute(
        "revoke all privileges on function "
        f"public.v2_validate_payment_instrument_binding() from public, {runtime}"
    )
    op.execute(
        "create trigger trg_payment_instrument_bindings_validate "
        "before insert or update on public.payment_instrument_bindings "
        "for each row execute function "
        "public.v2_validate_payment_instrument_binding()"
    )
    for table_name in (
        "payment_instruments",
        "payment_instrument_bindings",
        "payment_instrument_transactions",
    ):
        op.execute(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {runtime}"
        )
        op.execute(f"grant select on table public.{table_name} to {runtime}")
    op.execute(
        "grant insert on table public.payment_instruments to " f"{runtime}"
    )
    op.execute(
        "grant insert, update (status, effective_to) "
        "on table public.payment_instrument_bindings to " f"{runtime}"
    )
    op.execute(
        "grant insert on table public.payment_instrument_transactions to "
        f"{runtime}"
    )


def downgrade() -> None:
    runtime = _runtime_role()
    for table_name in (
        "payment_instrument_transactions",
        "payment_instrument_bindings",
        "payment_instruments",
    ):
        op.execute(
            f"revoke all privileges on table public.{table_name} "
            f"from public, {runtime}"
        )
    op.execute(
        "drop trigger trg_payment_instrument_bindings_validate "
        "on public.payment_instrument_bindings"
    )
    op.execute("drop function public.v2_validate_payment_instrument_binding()")
    op.drop_table("payment_instrument_transactions")
    op.drop_index(
        "ix_payment_instrument_bindings_resolution",
        table_name="payment_instrument_bindings",
    )
    op.drop_table("payment_instrument_bindings")
    op.drop_index(
        "ix_payment_instruments_book_name",
        table_name="payment_instruments",
    )
    op.drop_table("payment_instruments")
