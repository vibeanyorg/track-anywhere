"""Add the rebuildable monthly category summary projection."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0007_monthly_summary"
down_revision = "v2_0006_investment_lots"
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
    return value


def _quoted(identifier: str) -> str:
    return f'"{identifier}"'


def upgrade() -> None:
    runtime = _quoted(_runtime_role())
    op.create_table(
        "monthly_category_summaries",
        sa.Column("projection_name", sa.String(96), nullable=False),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("category_version_id", sa.Uuid(), nullable=False),
        sa.Column("asset_code", sa.String(16), nullable=False),
        sa.Column("line_kind", sa.String(32), nullable=False),
        sa.Column("units", sa.Numeric(48, 0), nullable=False),
        sa.Column("as_of_book_position", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id", "category_id", "category_version_id"],
            [
                "category_versions.book_id",
                "category_versions.category_id",
                "category_versions.category_version_id",
            ],
            name=op.f("fk_monthly_category_summaries_category_version"),
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "as_of_book_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name=op.f("fk_monthly_category_summaries_as_of_event"),
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["projection_name", "projector_version", "book_id", "generation"],
            [
                "projection_generations.projection_name",
                "projection_generations.projector_version",
                "projection_generations.book_id",
                "projection_generations.generation",
            ],
            name=op.f("fk_monthly_category_summaries_generation"),
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "projection_name = 'monthly_category_summary'",
            name=op.f("ck_monthly_category_summaries_projection_name_exact"),
        ),
        sa.CheckConstraint(
            "projector_version > 0",
            name=op.f("ck_monthly_category_summaries_projector_version_positive"),
        ),
        sa.CheckConstraint(
            "generation > 0",
            name=op.f("ck_monthly_category_summaries_generation_positive"),
        ),
        sa.CheckConstraint(
            "date_trunc('month', period_start)::date = period_start",
            name=op.f("ck_monthly_category_summaries_period_month_start"),
        ),
        sa.CheckConstraint(
            "line_kind in ('expense','income','transfer','tax','investment')",
            name=op.f("ck_monthly_category_summaries_line_kind_valid"),
        ),
        sa.CheckConstraint(
            "units <> 0", name=op.f("ck_monthly_category_summaries_units_nonzero")
        ),
        sa.CheckConstraint(
            "as_of_book_position > 0",
            name=op.f("ck_monthly_category_summaries_as_of_positive"),
        ),
        sa.PrimaryKeyConstraint(
            "projection_name",
            "projector_version",
            "book_id",
            "generation",
            "period_start",
            "category_id",
            "category_version_id",
            "asset_code",
            "line_kind",
            name=op.f("pk_monthly_category_summaries"),
        ),
    )
    op.create_index(
        op.f("ix_monthly_category_summaries_book_period"),
        "monthly_category_summaries",
        ["book_id", "generation", "period_start"],
    )
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        create function public.v2_guard_monthly_category_summary()
        returns trigger
        language plpgsql
        security invoker
        set search_path = pg_catalog, public
        as $function$
        begin
            if new.projection_name is distinct from old.projection_name
               or new.projector_version is distinct from old.projector_version
               or new.book_id is distinct from old.book_id
               or new.generation is distinct from old.generation
               or new.period_start is distinct from old.period_start
               or new.category_id is distinct from old.category_id
               or new.category_version_id is distinct from old.category_version_id
               or new.asset_code is distinct from old.asset_code
               or new.line_kind is distinct from old.line_kind then
                raise exception using errcode = '23514',
                    message = 'monthly category summary identity is immutable';
            end if;
            if new.as_of_book_position < old.as_of_book_position then
                raise exception using errcode = '23514',
                    message = 'monthly category summary cursor cannot move backward';
            end if;
            return new;
        end;
        $function$
        """
    )
    connection.exec_driver_sql(
        "create trigger trg_monthly_category_summaries_guard "
        "before update on public.monthly_category_summaries "
        "for each row execute function public.v2_guard_monthly_category_summary()"
    )
    connection.exec_driver_sql(
        f"revoke all privileges on table public.monthly_category_summaries from public, {runtime}"
    )
    connection.exec_driver_sql(
        f"grant select, insert, delete on table public.monthly_category_summaries to {runtime}"
    )
    connection.exec_driver_sql(
        f"grant update (units, as_of_book_position) on table public.monthly_category_summaries to {runtime}"
    )
    connection.exec_driver_sql(
        f"revoke all privileges on function public.v2_guard_monthly_category_summary() from public, {runtime}"
    )


def downgrade() -> None:
    raise RuntimeError(
        "the Track Anywhere V2 monthly summary migration is irreversible"
    )
