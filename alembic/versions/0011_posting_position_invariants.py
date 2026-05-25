"""add confirmed posting position invariants

Revision ID: 0011_posting_position_invariants
Revises: 0010_auth_machine_flows
Create Date: 2026-05-25 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0011_posting_position_invariants"
down_revision: Union[str, Sequence[str], None] = "0010_auth_machine_flows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_NAME = "uq_postings_transaction_position"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "postings" not in inspector.get_table_names():
        return
    _renumber_posting_positions()
    inspector = inspect(bind)
    if _posting_position_constraint_exists(inspector):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("postings") as batch_op:
            batch_op.create_unique_constraint(CONSTRAINT_NAME, ["transaction_id", "position"])
    else:
        op.create_unique_constraint(CONSTRAINT_NAME, "postings", ["transaction_id", "position"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "postings" not in inspector.get_table_names() or not _posting_position_constraint_exists(inspector):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("postings") as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
    else:
        op.drop_constraint(CONSTRAINT_NAME, "postings", type_="unique")


def _renumber_posting_positions() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("select id, transaction_id from postings order by transaction_id, position, id")
    ).fetchall()
    next_positions: dict[str, int] = {}
    for posting_id, transaction_id in rows:
        position = next_positions.get(transaction_id, 0)
        connection.execute(
            sa.text("update postings set position = :position where id = :posting_id"),
            {"position": position, "posting_id": posting_id},
        )
        next_positions[transaction_id] = position + 1


def _posting_position_constraint_exists(inspector) -> bool:
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("postings")
        if constraint.get("name")
    }
    if CONSTRAINT_NAME in unique_constraints:
        return True
    return any(
        index.get("name") == CONSTRAINT_NAME and index.get("unique")
        for index in inspector.get_indexes("postings")
    )
