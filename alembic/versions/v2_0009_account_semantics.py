"""Add strict account type and product subtype semantics."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "v2_0009_account_semantics"
down_revision = "v2_0007_monthly_summary"
branch_labels = None
depends_on = None

_ACCOUNT_TYPES = (
    "asset",
    "liability",
    "equity",
    "income",
    "expense",
    "fund",
    "system",
)


def _replace_account_identity_guard(*, include_semantics: bool) -> None:
    semantic_guard = """
               or new.account_type is distinct from old.account_type
               or new.account_subtype is distinct from old.account_subtype"""
    if not include_semantics:
        semantic_guard = ""
    op.execute(
        f"""
        create or replace function public.v2_guard_account_identity()
        returns trigger
        language plpgsql
        security invoker
        set search_path = pg_catalog, public
        as $function$
        begin
            if new.book_id is distinct from old.book_id
               or new.account_id is distinct from old.account_id
               or new.asset_code is distinct from old.asset_code
               or new.system_role is distinct from old.system_role
               {semantic_guard} then
                raise exception using
                    errcode = '23514',
                    message = 'account accounting identity is immutable';
            end if;
            return new;
        end;
        $function$
        """
    )


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("account_subtype", sa.String(length=64), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_accounts_account_type_nonblank"),
        "accounts",
        type_="check",
    )
    quoted_types = ",".join(f"'{account_type}'" for account_type in _ACCOUNT_TYPES)
    op.create_check_constraint(
        op.f("ck_accounts_account_type_valid"),
        "accounts",
        f"account_type in ({quoted_types})",
    )
    op.create_check_constraint(
        op.f("ck_accounts_account_subtype_valid"),
        "accounts",
        "account_subtype is null or account_subtype ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'",
    )
    op.create_check_constraint(
        op.f("ck_accounts_credit_card_type_valid"),
        "accounts",
        "account_subtype <> 'credit_card' or account_type = 'liability'",
    )
    _replace_account_identity_guard(include_semantics=True)


def downgrade() -> None:
    _replace_account_identity_guard(include_semantics=False)
    op.drop_constraint(
        op.f("ck_accounts_credit_card_type_valid"),
        "accounts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_accounts_account_subtype_valid"),
        "accounts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_accounts_account_type_valid"),
        "accounts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_accounts_account_type_nonblank"),
        "accounts",
        "btrim(account_type) <> ''",
    )
    op.drop_column("accounts", "account_subtype")
