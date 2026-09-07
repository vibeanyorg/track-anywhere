"""Versioned payment instrument configuration and nonoverlapping bindings."""

import os
import re

from alembic import op
import sqlalchemy as sa

revision = "v2_0017_card_lifecycle"
down_revision = "v2_0016_journal_pagination_index"
branch_labels = None
depends_on = None


def _runtime_role() -> str:
    role = os.environ.get("TRACK_ANYWHERE_DB_RUNTIME_ROLE", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise RuntimeError("safe runtime role required")
    return f'"{role}"'


def upgrade() -> None:
    runtime = _runtime_role()
    op.add_column(
        "payment_instruments",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "payment_instruments",
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )
    op.add_column(
        "payment_instruments", sa.Column("effective_to", sa.DateTime(timezone=True))
    )
    op.execute(
        "update payment_instruments i set effective_from = coalesce((select min(b.effective_from) from payment_instrument_bindings b where b.book_id=i.book_id and b.instrument_id=i.instrument_id), i.created_at)"
    )
    op.create_table(
        "payment_instrument_mutations",
        sa.Column("book_id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("actor_subject_id", sa.Text(), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "instrument_id"],
            ["payment_instruments.book_id", "payment_instruments.instrument_id"],
            ondelete="RESTRICT",
        ),
    )
    op.execute(f"revoke all on payment_instrument_mutations from public, {runtime}")
    op.execute(f"grant select, insert on payment_instrument_mutations to {runtime}")
    op.execute(
        f"grant update (current_name, network, provider_code, form_factor, last4, status, version, effective_from, effective_to, updated_at) on payment_instruments to {runtime}"
    )
    # Serialize writes on the parent row, including callers outside the application.
    op.execute("""create function public.v2_no_overlapping_card_binding() returns trigger
    language plpgsql security invoker set search_path = pg_catalog, public as $fn$
    begin
      perform 1 from public.payment_instruments where book_id=new.book_id
        and instrument_id=new.instrument_id for update;
      if exists(select 1 from public.payment_instrument_bindings b
        where b.book_id=new.book_id and b.instrument_id=new.instrument_id
        and b.asset_code=new.asset_code and b.binding_id<>new.binding_id
        and tstzrange(b.effective_from,b.effective_to,'[)') && tstzrange(new.effective_from,new.effective_to,'[)')) then
        raise exception using errcode='23514', message='overlapping settlement binding for asset_code';
      end if;
      return new;
    end; $fn$""")
    op.execute(
        "create trigger trg_card_binding_overlap before insert or update on payment_instrument_bindings for each row execute function public.v2_no_overlapping_card_binding()"
    )
    op.execute(
        f"revoke all on function public.v2_no_overlapping_card_binding() from public, {runtime}"
    )


def downgrade() -> None:
    runtime = _runtime_role()
    op.execute("drop trigger trg_card_binding_overlap on payment_instrument_bindings")
    op.execute("drop function public.v2_no_overlapping_card_binding()")
    op.execute(
        f"revoke update (current_name, network, provider_code, form_factor, last4, status, version, effective_from, effective_to, updated_at) on payment_instruments from {runtime}"
    )
    op.drop_table("payment_instrument_mutations")
    for column in ("effective_to", "effective_from", "version"):
        op.drop_column("payment_instruments", column)
