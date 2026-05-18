"""books, category nodes, transaction lines, and budget targets

Revision ID: 0003_domain_redesign
Revises: 0002_recurring
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_domain_redesign"
down_revision: Union[str, Sequence[str], None] = "0002_recurring"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ledger_books",
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("base_currency", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("book_id"),
    )
    op.create_table(
        "book_members",
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("book_id", "user_id"),
    )
    for table_name in ("accounts", "transactions", "drafts", "funds", "recurring_items", "investment_events", "categories"):
        op.add_column(table_name, sa.Column("book_id", sa.String(length=80), nullable=False, server_default="book_default"))
    op.add_column("categories", sa.Column("parent_id", sa.String(length=80), nullable=True))
    op.add_column("categories", sa.Column("name", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("categories", sa.Column("normalized_name", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("categories", sa.Column("level", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("categories", sa.Column("path_cache", sa.String(length=180), nullable=False, server_default=""))
    op.add_column("categories", sa.Column("icon", sa.String(length=80), nullable=True))
    op.add_column("categories", sa.Column("color", sa.String(length=32), nullable=True))
    op.add_column("categories", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("categories", sa.Column("status", sa.String(length=40), nullable=False, server_default="active"))
    op.create_table(
        "category_aliases",
        sa.Column("alias_id", sa.String(length=80), nullable=False),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("category_id", sa.String(length=80), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False),
        sa.Column("normalized_alias", sa.String(length=80), nullable=False),
        sa.Column("locale", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("alias_id"),
    )
    op.create_table(
        "category_versions",
        sa.Column("category_version_id", sa.String(length=80), nullable=False),
        sa.Column("category_id", sa.String(length=80), nullable=False),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("parent_id", sa.String(length=80), nullable=True),
        sa.Column("path", sa.String(length=180), nullable=False),
        sa.Column("icon", sa.String(length=80), nullable=True),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("valid_from", sa.String(length=80), nullable=False),
        sa.Column("valid_to", sa.String(length=80), nullable=True),
        sa.Column("change_reason", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("category_version_id"),
    )
    op.create_table(
        "classification_events",
        sa.Column("classification_event_id", sa.String(length=80), nullable=False),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source_category_id", sa.String(length=80), nullable=True),
        sa.Column("target_category_id", sa.String(length=80), nullable=True),
        sa.Column("affected_line_count", sa.Integer(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("rollback", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("classification_event_id"),
    )
    op.create_table(
        "transaction_lines",
        sa.Column("line_id", sa.String(length=80), nullable=False),
        sa.Column("transaction_id", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("line_type", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("category_id", sa.String(length=80), nullable=True),
        sa.Column("category_version_id", sa.String(length=80), nullable=True),
        sa.Column("category_path_snapshot", sa.JSON(), nullable=True),
        sa.Column("merchant_id", sa.String(length=80), nullable=True),
        sa.Column("project_id", sa.String(length=80), nullable=True),
        sa.Column("necessity", sa.String(length=40), nullable=False),
        sa.Column("reimbursement_status", sa.String(length=40), nullable=False),
        sa.Column("memo", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.transaction_id"]),
        sa.PrimaryKeyConstraint("line_id"),
    )
    op.create_table(
        "budgets",
        sa.Column("budget_id", sa.String(length=80), nullable=False),
        sa.Column("book_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("period", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("total_amount", sa.String(length=80), nullable=False),
        sa.Column("starts_on", sa.String(length=20), nullable=True),
        sa.Column("ends_on", sa.String(length=20), nullable=True),
        sa.Column("rollover_policy", sa.String(length=40), nullable=False),
        sa.Column("alert_thresholds", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("budget_id"),
    )
    op.create_table(
        "budget_targets",
        sa.Column("budget_target_id", sa.String(length=80), nullable=False),
        sa.Column("budget_id", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=80), nullable=True),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.String(length=80), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("budget_target_id"),
    )


def downgrade() -> None:
    op.drop_table("budget_targets")
    op.drop_table("budgets")
    op.drop_table("transaction_lines")
    op.drop_table("classification_events")
    op.drop_table("category_versions")
    op.drop_table("category_aliases")
    for column_name in ("status", "sort_order", "color", "icon", "path_cache", "level", "normalized_name", "name", "parent_id"):
        op.drop_column("categories", column_name)
    for table_name in ("categories", "investment_events", "recurring_items", "funds", "drafts", "transactions", "accounts"):
        op.drop_column(table_name, "book_id")
    op.drop_table("book_members")
    op.drop_table("ledger_books")
