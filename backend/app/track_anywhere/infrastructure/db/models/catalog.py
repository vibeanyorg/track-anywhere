from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


_NOW = text("clock_timestamp()")


class AssetRecord(V2Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("btrim(kind) <> ''", name="kind_nonblank"),
        CheckConstraint("ledger_scale between 0 and 30", name="ledger_scale_range"),
        CheckConstraint(
            "input_scale between 0 and ledger_scale", name="input_scale_range"
        ),
        CheckConstraint(
            "display_scale between 0 and ledger_scale", name="display_scale_range"
        ),
        CheckConstraint("btrim(current_name) <> ''", name="current_name_nonblank"),
        CheckConstraint("status in ('active', 'disabled')", name="status_valid"),
    )

    asset_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    ledger_scale: Mapped[int] = mapped_column(SmallInteger)
    input_scale: Mapped[int] = mapped_column(SmallInteger)
    display_scale: Mapped[int] = mapped_column(SmallInteger)
    current_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class BookRecord(V2Base):
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint("btrim(current_name) <> ''", name="current_name_nonblank"),
        CheckConstraint(
            "write_state in ('active', 'paused_integrity')",
            name="write_state_valid",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    current_name: Mapped[str] = mapped_column(Text)
    base_asset_code: Mapped[str | None] = mapped_column(
        String(16),
        ForeignKey(
            "assets.asset_code",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    write_state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class AccountRecord(V2Base):
    __tablename__ = "accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["asset_code"],
            ["assets.asset_code"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "account_id",
            "asset_code",
            name="uq_accounts_book_account_asset",
        ),
        CheckConstraint("btrim(account_type) <> ''", name="account_type_nonblank"),
        CheckConstraint(
            "system_role is null or btrim(system_role) <> ''",
            name="system_role_nonblank",
        ),
        CheckConstraint("btrim(current_name) <> ''", name="current_name_nonblank"),
        CheckConstraint("status in ('active', 'closed')", name="status_valid"),
        Index(
            "ux_accounts_system_role",
            "book_id",
            "asset_code",
            "system_role",
            unique=True,
            postgresql_where=text("system_role IS NOT NULL"),
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[UUID] = mapped_column(primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(16))
    account_type: Mapped[str] = mapped_column(String(32))
    system_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class CategoryRecord(V2Base):
    __tablename__ = "categories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            name="fk_categories_book",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "parent_category_id"],
            ["categories.book_id", "categories.category_id"],
            name="fk_categories_parent_category",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "category_id", "current_version_id"],
            [
                "category_versions.book_id",
                "category_versions.category_id",
                "category_versions.category_version_id",
            ],
            name="fk_categories_current_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("btrim(current_name) <> ''", name="current_name_nonblank"),
        CheckConstraint("status in ('active', 'archived')", name="status_valid"),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    category_id: Mapped[UUID] = mapped_column(primary_key=True)
    parent_category_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_name: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class CategoryVersionRecord(V2Base):
    __tablename__ = "category_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "category_id"],
            ["categories.book_id", "categories.category_id"],
            name="fk_category_versions_category",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "parent_category_id"],
            ["categories.book_id", "categories.category_id"],
            name="fk_category_versions_parent_category",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
        CheckConstraint("status in ('active', 'archived')", name="status_valid"),
        CheckConstraint(
            "btrim(change_reason_code) <> ''", name="change_reason_nonblank"
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    category_id: Mapped[UUID] = mapped_column(primary_key=True)
    category_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    parent_category_id: Mapped[UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    change_reason_code: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


__all__ = [
    "AccountRecord",
    "AssetRecord",
    "BookRecord",
    "CategoryRecord",
    "CategoryVersionRecord",
]
