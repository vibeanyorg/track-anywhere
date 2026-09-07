"""Add versioned, audited account metadata mutations."""

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0018_account_rename"
down_revision = "v2_0017_card_lifecycle"
branch_labels = None
depends_on = None


def _runtime_role() -> str:
    role = os.environ.get("TRACK_ANYWHERE_DB_RUNTIME_ROLE", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise RuntimeError("safe runtime role required")
    return f'"{role}"'


def upgrade() -> None:
    runtime = _runtime_role()
    op.add_column(
        "accounts",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "account_mutations",
        sa.Column("book_id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("actor_subject_id", sa.Text(), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id", "account_id"],
            ["accounts.book_id", "accounts.account_id"],
            ondelete="RESTRICT",
        ),
    )
    op.execute(f"revoke all on account_mutations from public, {runtime}")
    op.execute(f"grant select, insert on account_mutations to {runtime}")
    op.execute(f"revoke update on accounts from {runtime}")
    op.execute(
        f"grant update (current_name, status, version, updated_at) on accounts to {runtime}"
    )


def downgrade() -> None:
    runtime = _runtime_role()
    op.execute(
        f"revoke update (current_name, status, version, updated_at) on accounts from {runtime}"
    )
    op.execute(f"grant update on accounts to {runtime}")
    op.drop_table("account_mutations")
    op.drop_column("accounts", "version")
