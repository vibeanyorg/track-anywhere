"""enforce canonical debit credit posting shape constraints

Revision ID: 0019_posting_constraints
Revises: 0018_debit_credit_defaults
Create Date: 2026-06-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0019_posting_constraints"
down_revision: Union[str, Sequence[str], None] = "0018_debit_credit_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POSTING_CONSTRAINTS = (
    (
        "ck_postings_debit_credit_shape",
        "amount_semantics != 'debit_credit' or (side in ('debit', 'credit') and cast(amount as numeric) > 0)",
    ),
)

DRAFT_POSTING_CONSTRAINTS = (
    (
        "ck_draft_postings_debit_credit_shape",
        "amount_semantics != 'debit_credit' or (side in ('debit', 'credit') and cast(amount as numeric) > 0)",
    ),
)


def upgrade() -> None:
    _create_missing_constraints("postings", POSTING_CONSTRAINTS)
    _create_missing_constraints("draft_postings", DRAFT_POSTING_CONSTRAINTS)


def downgrade() -> None:
    _drop_existing_constraints("draft_postings", DRAFT_POSTING_CONSTRAINTS)
    _drop_existing_constraints("postings", POSTING_CONSTRAINTS)


def _create_missing_constraints(table_name: str, constraints: tuple[tuple[str, str], ...]) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    existing = _existing_check_constraint_names(inspector, table_name)
    missing = [(name, condition) for name, condition in constraints if name not in existing]
    if not missing:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch_op:
            for name, condition in missing:
                batch_op.create_check_constraint(name, condition)
        return
    for name, condition in missing:
        op.create_check_constraint(name, table_name, condition)


def _drop_existing_constraints(table_name: str, constraints: tuple[tuple[str, str], ...]) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return
    existing = _existing_check_constraint_names(inspector, table_name)
    names = [name for name, _condition in constraints if name in existing]
    if not names:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch_op:
            for name in names:
                batch_op.drop_constraint(name, type_="check")
        return
    for name in names:
        op.drop_constraint(name, table_name, type_="check")


def _existing_check_constraint_names(inspector, table_name: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints(table_name)
        if constraint.get("name")
    }
