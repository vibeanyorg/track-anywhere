"""Add the journal cursor pagination index."""

from __future__ import annotations

from alembic import op


revision = "v2_0016_journal_pagination_index"
down_revision = "v2_0015_payment_instruments"
branch_labels = None
depends_on = None


_INDEX_NAME = "ix_journal_transactions_book_effective_position"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "journal_transactions",
        ["book_id", "effective_at", "source_position", "transaction_id"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="journal_transactions")
