"""transaction line counterparties

Revision ID: 0015_transaction_line_counterparties
Revises: 0014_counterparties
Create Date: 2026-05-27 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0015_transaction_line_counterparties"
down_revision: Union[str, Sequence[str], None] = "0014_counterparties"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "transaction_lines" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("transaction_lines")}
    columns = {column["name"] for column in inspector.get_columns("transaction_lines")}
    if "counterparty_id" not in columns:
        with op.batch_alter_table("transaction_lines") as batch_op:
            batch_op.add_column(sa.Column("counterparty_id", sa.String(length=80), nullable=True))
    if "merchant_id" in columns:
        op.execute(
            "update transaction_lines "
            "set counterparty_id = merchant_id "
            "where counterparty_id is null and merchant_id is not null"
        )
        with op.batch_alter_table("transaction_lines") as batch_op:
            batch_op.drop_column("merchant_id")
    if "ix_transaction_lines_book_counterparty" not in indexes:
        op.create_index(
            "ix_transaction_lines_book_counterparty",
            "transaction_lines",
            ["book_id", "counterparty_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "transaction_lines" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("transaction_lines")}
    columns = {column["name"] for column in inspector.get_columns("transaction_lines")}
    if "ix_transaction_lines_book_counterparty" in indexes:
        op.drop_index("ix_transaction_lines_book_counterparty", table_name="transaction_lines")
    if "merchant_id" not in columns:
        with op.batch_alter_table("transaction_lines") as batch_op:
            batch_op.add_column(sa.Column("merchant_id", sa.String(length=80), nullable=True))
    if "counterparty_id" in columns:
        op.execute(
            "update transaction_lines "
            "set merchant_id = counterparty_id "
            "where merchant_id is null and counterparty_id is not null"
        )
        with op.batch_alter_table("transaction_lines") as batch_op:
            batch_op.drop_column("counterparty_id")
