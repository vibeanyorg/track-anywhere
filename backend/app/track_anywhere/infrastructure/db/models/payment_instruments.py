from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


_NOW = text("clock_timestamp()")


class PaymentInstrumentRecord(V2Base):
    __tablename__ = "payment_instruments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("instrument_kind = 'card'", name="kind_valid"),
        CheckConstraint(
            "form_factor in ('virtual','physical','single_use')",
            name="form_factor_valid",
        ),
        CheckConstraint(
            "network in ('mastercard','visa','amex','unionpay','other')",
            name="network_valid",
        ),
        CheckConstraint(
            "settlement_policy in ('immediate','prepaid','statement')",
            name="settlement_policy_valid",
        ),
        CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name="provider_code_valid",
        ),
        CheckConstraint("btrim(current_name) <> ''", name="current_name_nonblank"),
        CheckConstraint(
            "last4 is null or last4 ~ '^[0-9]{4}$'",
            name="last4_valid",
        ),
        CheckConstraint(
            "status in ('active','frozen','closed')",
            name="status_valid",
        ),
        Index(
            "ix_payment_instruments_book_name",
            "book_id",
            "current_name",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(primary_key=True)
    instrument_kind: Mapped[str] = mapped_column(String(16), default="card")
    form_factor: Mapped[str] = mapped_column(String(16))
    network: Mapped[str] = mapped_column(String(16))
    provider_code: Mapped[str] = mapped_column(String(32))
    settlement_policy: Mapped[str] = mapped_column(String(16))
    current_name: Mapped[str] = mapped_column(Text)
    last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class PaymentInstrumentBindingRecord(V2Base):
    __tablename__ = "payment_instrument_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "instrument_id"],
            ["payment_instruments.book_id", "payment_instruments.instrument_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "binding_role in ('funding_asset','card_liability')",
            name="role_valid",
        ),
        CheckConstraint("priority > 0", name="priority_positive"),
        CheckConstraint(
            "status in ('active','closed')",
            name="status_valid",
        ),
        CheckConstraint(
            "effective_to is null or effective_to > effective_from",
            name="effective_window_valid",
        ),
        UniqueConstraint(
            "book_id",
            "binding_id",
            "instrument_id",
            name="uq_payment_instrument_bindings_instrument",
        ),
        Index(
            "ix_payment_instrument_bindings_resolution",
            "book_id",
            "instrument_id",
            "asset_code",
            "status",
            "priority",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    binding_id: Mapped[UUID] = mapped_column(primary_key=True)
    instrument_id: Mapped[UUID]
    account_id: Mapped[UUID]
    asset_code: Mapped[str] = mapped_column(String(16))
    binding_role: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(16), default="active")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


class PaymentInstrumentTransactionRecord(V2Base):
    __tablename__ = "payment_instrument_transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "instrument_id"],
            ["payment_instruments.book_id", "payment_instruments.instrument_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "binding_id", "instrument_id"],
            [
                "payment_instrument_bindings.book_id",
                "payment_instrument_bindings.binding_id",
                "payment_instrument_bindings.instrument_id",
            ],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    instrument_id: Mapped[UUID]
    binding_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


__all__ = [
    "PaymentInstrumentBindingRecord",
    "PaymentInstrumentRecord",
    "PaymentInstrumentTransactionRecord",
]
