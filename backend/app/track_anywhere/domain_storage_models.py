from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .storage_models import ASSET_CODE_LENGTH, Base


class LedgerBookRecord(Base):
    __tablename__ = "ledger_books"

    book_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    base_currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    timezone: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    template_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)


class BookMemberRecord(Base):
    __tablename__ = "book_members"

    book_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)


class TransactionLineRecord(Base):
    __tablename__ = "transaction_lines"
    __table_args__ = (
        Index("ix_transaction_lines_book_category", "book_id", "category_id"),
        Index("ix_transaction_lines_book_counterparty", "book_id", "counterparty_id"),
    )

    line_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.transaction_id"))
    position: Mapped[int] = mapped_column(Integer)
    line_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    book_id: Mapped[str] = mapped_column(String(80))
    category_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category_path_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    counterparty_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    necessity: Mapped[str] = mapped_column(String(40))
    reimbursement_status: Mapped[str] = mapped_column(String(40))
    memo: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer)


class CategoryAliasRecord(Base):
    __tablename__ = "category_aliases"

    alias_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80))
    category_id: Mapped[str] = mapped_column(String(80))
    alias: Mapped[str] = mapped_column(String(80))
    normalized_alias: Mapped[str] = mapped_column(String(80))
    locale: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)


class CategoryVersionRecord(Base):
    __tablename__ = "category_versions"

    category_version_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    category_id: Mapped[str] = mapped_column(String(80))
    book_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(80))
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    path: Mapped[str] = mapped_column(String(180))
    icon: Mapped[str | None] = mapped_column(String(80), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid_from: Mapped[str] = mapped_column(String(80))
    valid_to: Mapped[str | None] = mapped_column(String(80), nullable=True)
    change_reason: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)


class ClassificationEventRecord(Base):
    __tablename__ = "classification_events"

    classification_event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(80))
    source_category_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_category_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    affected_line_count: Mapped[int] = mapped_column(Integer)
    before: Mapped[dict[str, Any]] = mapped_column(JSON)
    after: Mapped[dict[str, Any]] = mapped_column(JSON)
    rollback: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)


class BudgetRecord(Base):
    __tablename__ = "budgets"

    budget_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    period: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    total_amount: Mapped[str] = mapped_column(String(80))
    starts_on: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ends_on: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rollover_policy: Mapped[str] = mapped_column(String(40))
    alert_thresholds: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)


class BudgetTargetRecord(Base):
    __tablename__ = "budget_targets"

    budget_target_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    budget_id: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mode: Mapped[str] = mapped_column(String(40))
    amount: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[dict[str, str]] = mapped_column("metadata", JSON)
    version: Mapped[int] = mapped_column(Integer)
