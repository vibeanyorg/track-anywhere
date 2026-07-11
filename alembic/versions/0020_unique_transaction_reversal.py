"""enforce one reversal per original transaction

Revision ID: 0020_unique_transaction_reversal
Revises: 0019_posting_constraints
Create Date: 2026-07-11 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "0020_unique_transaction_reversal"
down_revision: Union[str, Sequence[str], None] = "0019_posting_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "uq_transactions_reverses_transaction_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "transactions" not in inspector.get_table_names():
        return
    existing = {item.get("name") for item in inspector.get_unique_constraints("transactions")}
    if CONSTRAINT_NAME in existing:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("transactions") as batch_op:
            batch_op.create_unique_constraint(CONSTRAINT_NAME, ["reverses_transaction_id"])
        return
    op.create_unique_constraint(CONSTRAINT_NAME, "transactions", ["reverses_transaction_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "transactions" not in inspector.get_table_names():
        return
    existing = {item.get("name") for item in inspector.get_unique_constraints("transactions")}
    if CONSTRAINT_NAME not in existing:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("transactions") as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
        return
    op.drop_constraint(CONSTRAINT_NAME, "transactions", type_="unique")
