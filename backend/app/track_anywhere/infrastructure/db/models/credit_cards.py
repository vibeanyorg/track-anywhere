from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class CreditCardTransactionRecord(V2Base):
    """Atomic semantic relation projected with the immutable journal entry."""

    __tablename__ = "credit_card_transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_credit_card_transactions_journal_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_credit_card_transactions_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_credit_card_transactions_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "card_account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_credit_card_transactions_card_account",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "counter_account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_credit_card_transactions_counter_account",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "original_transaction_id"],
            [
                "credit_card_transactions.book_id",
                "credit_card_transactions.transaction_id",
            ],
            name="fk_credit_card_transactions_original",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "source_event_id",
            name="uq_credit_card_transactions_book_source_event",
        ),
        CheckConstraint(
            "intent in ('charge', 'payment', 'refund', 'fee')",
            name="intent_valid",
        ),
        CheckConstraint(
            "(intent = 'refund' and original_transaction_id is not null) or "
            "(intent <> 'refund' and original_transaction_id is null)",
            name="original_shape_valid",
        ),
        CheckConstraint(
            "card_account_id <> counter_account_id",
            name="accounts_distinct",
        ),
        CheckConstraint("units > 0", name="units_positive"),
        CheckConstraint("source_position > 0", name="source_position_positive"),
        Index(
            "ix_credit_card_transactions_active_refunds",
            "book_id",
            "original_transaction_id",
            "source_position",
            postgresql_where=text("original_transaction_id is not null"),
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    intent: Mapped[str] = mapped_column(String(16))
    card_account_id: Mapped[UUID] = mapped_column()
    counter_account_id: Mapped[UUID] = mapped_column()
    asset_code: Mapped[str] = mapped_column(String(16))
    units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    original_transaction_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_event_id: Mapped[UUID] = mapped_column()
    source_position: Mapped[int] = mapped_column(BigInteger)


__all__ = ["CreditCardTransactionRecord"]
