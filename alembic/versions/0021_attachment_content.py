"""persist attachment content

Revision ID: 0021_attachment_content
Revises: 0020_unique_transaction_reversal
Create Date: 2026-07-11 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0021_attachment_content"
down_revision: Union[str, Sequence[str], None] = "0020_unique_transaction_reversal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "attachments" not in inspector.get_table_names():
        return
    if "content" in {column["name"] for column in inspector.get_columns("attachments")}:
        return
    op.add_column("attachments", sa.Column("content", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "attachments" not in inspector.get_table_names():
        return
    if "content" not in {column["name"] for column in inspector.get_columns("attachments")}:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("attachments") as batch_op:
            batch_op.drop_column("content")
        return
    op.drop_column("attachments", "content")
