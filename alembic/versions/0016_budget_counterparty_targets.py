"""budget counterparty targets

Revision ID: 0016_budget_counterparty_targets
Revises: 0015_transaction_line_counterparties
Create Date: 2026-05-27 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "0016_budget_counterparty_targets"
down_revision: Union[str, Sequence[str], None] = "0015_transaction_line_counterparties"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "budget_targets" not in inspect(bind).get_table_names():
        return
    op.execute("update budget_targets set target_type = 'counterparty' where target_type = 'merchant'")


def downgrade() -> None:
    bind = op.get_bind()
    if "budget_targets" not in inspect(bind).get_table_names():
        return
    op.execute("update budget_targets set target_type = 'merchant' where target_type = 'counterparty'")
