"""split transaction memo from purpose

Revision ID: 0006_split_memo_purpose
Revises: 0005_password_accounts
Create Date: 2026-05-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0006_split_memo_purpose"
down_revision: Union[str, Sequence[str], None] = "0005_password_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        update transaction_lines
        set memo = ''
        where memo <> ''
          and exists (
            select 1
            from transactions
            where transactions.transaction_id = transaction_lines.transaction_id
              and transaction_lines.memo = transactions.purpose
          )
        """
    )
    op.execute("update transactions set memo = '' where memo <> '' and memo = purpose")


def downgrade() -> None:
    pass
