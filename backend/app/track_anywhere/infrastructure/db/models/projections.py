from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


_NOW = text("clock_timestamp()")
_POSTING_SIDE = postgresql.ENUM(
    "debit", "credit", name="posting_side", create_type=False
)


class SynchronousProjectionEventTypeRecord(V2Base):
    __tablename__ = "synchronous_projection_event_types"
    __table_args__ = (
        CheckConstraint("btrim(event_type) <> ''", name="event_type_nonblank"),
        CheckConstraint("event_schema_version > 0", name="schema_version_positive"),
    )

    event_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_schema_version: Mapped[int] = mapped_column(SmallInteger, primary_key=True)


class JournalTransactionRecord(V2Base):
    __tablename__ = "journal_transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_journal_transactions_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_journal_transactions_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "description_ref"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_journal_transactions_description",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "source_event_id",
            name="uq_journal_transactions_book_source_event",
        ),
        UniqueConstraint(
            "book_id",
            "transaction_id",
            "source_event_id",
            name="uq_journal_transactions_transaction_source",
        ),
        CheckConstraint("source_position > 0", name="source_position_positive"),
        CheckConstraint(
            "transaction_kind in ('standard', 'opening', 'adjustment', "
            "'transfer', 'fx', 'investment_cash')",
            name="kind_valid",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_event_id: Mapped[UUID] = mapped_column()
    source_position: Mapped[int] = mapped_column(BigInteger)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transaction_kind: Mapped[str] = mapped_column(String(32))
    description_ref: Mapped[UUID | None] = mapped_column(nullable=True)


class JournalPostingRecord(V2Base):
    __tablename__ = "journal_postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_journal_postings_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_journal_postings_account_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "transaction_id",
            "posting_position",
            name="uq_journal_postings_transaction_position",
        ),
        CheckConstraint("posting_position >= 0", name="position_nonnegative"),
        CheckConstraint("units > 0", name="units_positive"),
        Index(
            "ix_journal_postings_account_transaction",
            "book_id",
            "account_id",
            "transaction_id",
        ),
        Index(
            "ix_journal_postings_transaction_asset_side",
            "book_id",
            "transaction_id",
            "asset_code",
            "side",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column()
    posting_id: Mapped[UUID] = mapped_column(primary_key=True)
    posting_position: Mapped[int] = mapped_column(SmallInteger)
    account_id: Mapped[UUID] = mapped_column()
    asset_code: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(_POSTING_SIDE)
    units: Mapped[Decimal] = mapped_column(Numeric(38, 0))


class AccountBalanceRecord(V2Base):
    __tablename__ = "account_balances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "account_id", "asset_code"],
            ["accounts.book_id", "accounts.account_id", "accounts.asset_code"],
            name="fk_account_balances_account_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "as_of_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_account_balances_as_of_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        CheckConstraint("as_of_position > 0", name="as_of_position_positive"),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[UUID] = mapped_column(primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    balance_units: Mapped[Decimal] = mapped_column(Numeric(48, 0))
    as_of_position: Mapped[int] = mapped_column(BigInteger)


class TransactionReversalRecord(V2Base):
    __tablename__ = "transaction_reversals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "reversal_transaction_id", "source_event_id"],
            [
                "journal_transactions.book_id",
                "journal_transactions.transaction_id",
                "journal_transactions.source_event_id",
            ],
            name="fk_transaction_reversals_reversal_source",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "original_transaction_id", "original_event_id"],
            [
                "journal_transactions.book_id",
                "journal_transactions.transaction_id",
                "journal_transactions.source_event_id",
            ],
            name="fk_transaction_reversals_original_source",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "original_transaction_id",
            name="uq_transaction_reversals_original_target",
        ),
        CheckConstraint(
            "reversal_transaction_id <> original_transaction_id",
            name="distinct_transactions",
        ),
        CheckConstraint(
            "octet_length(original_event_hash) = 32", name="original_hash_length"
        ),
        CheckConstraint(
            "reason_code in ('user_correction', 'duplicate', "
            "'import_correction', 'provider_reversal')",
            name="reason_valid",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    reversal_transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    original_transaction_id: Mapped[UUID] = mapped_column()
    source_event_id: Mapped[UUID] = mapped_column()
    original_event_id: Mapped[UUID] = mapped_column()
    original_event_hash: Mapped[bytes] = mapped_column(LargeBinary)
    reason_code: Mapped[str] = mapped_column(String(32))


class TransactionExternalReferenceRecord(V2Base):
    __tablename__ = "transaction_external_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_transaction_external_references_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_transaction_external_references_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "provider_code ~ '^[a-z][a-z0-9_-]{0,31}$'", name="provider_valid"
        ),
        CheckConstraint(
            "reference_kind in ('provider_transaction', 'bank_transaction', "
            "'card_transaction', 'broker_trade')",
            name="kind_valid",
        ),
        CheckConstraint(
            "reference_value ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'",
            name="value_valid",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    provider_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    reference_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    reference_value: Mapped[str] = mapped_column(String(128))
    source_event_id: Mapped[UUID] = mapped_column()


class ReportingLineRecord(V2Base):
    __tablename__ = "reporting_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "transaction_id"],
            ["journal_transactions.book_id", "journal_transactions.transaction_id"],
            name="fk_reporting_lines_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["asset_code"],
            ["assets.asset_code"],
            name="fk_reporting_lines_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_reporting_lines_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "description_ref"],
            [
                "protected_description_sidecars.book_id",
                "protected_description_sidecars.sidecar_id",
            ],
            name="fk_reporting_lines_description",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "transaction_id",
            "classification_revision",
            "line_position",
            name="uq_reporting_lines_revision_position",
        ),
        UniqueConstraint(
            "book_id",
            "transaction_id",
            "line_version_id",
            name="uq_reporting_lines_version",
        ),
        CheckConstraint("classification_revision > 0", name="revision_positive"),
        CheckConstraint("line_position >= 0", name="position_nonnegative"),
        CheckConstraint("units > 0", name="units_positive"),
        CheckConstraint(
            "line_kind in ('expense', 'income', 'transfer', 'tax', 'investment')",
            name="kind_valid",
        ),
        CheckConstraint(
            "dimension in ('category', 'project', 'counterparty', 'tax')",
            name="dimension_valid",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[UUID] = mapped_column(primary_key=True)
    classification_revision: Mapped[int] = mapped_column(Integer)
    line_id: Mapped[UUID] = mapped_column(primary_key=True)
    line_version_id: Mapped[UUID] = mapped_column()
    catalog_id: Mapped[UUID] = mapped_column()
    line_position: Mapped[int] = mapped_column(SmallInteger)
    asset_code: Mapped[str] = mapped_column(String(16))
    units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    line_kind: Mapped[str] = mapped_column(String(32))
    dimension: Mapped[str] = mapped_column(String(32))
    dimension_id: Mapped[UUID | None] = mapped_column(nullable=True)
    description_ref: Mapped[UUID | None] = mapped_column(nullable=True)
    source_event_id: Mapped[UUID] = mapped_column()


class SynchronousProjectionAppliedEventRecord(V2Base):
    __tablename__ = "synchronous_projection_applied_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_synchronous_projection_applied_events_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        CheckConstraint("projection_version > 0", name="version_positive"),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW
    )


__all__ = [
    "AccountBalanceRecord",
    "JournalPostingRecord",
    "JournalTransactionRecord",
    "ReportingLineRecord",
    "SynchronousProjectionAppliedEventRecord",
    "SynchronousProjectionEventTypeRecord",
    "TransactionExternalReferenceRecord",
    "TransactionReversalRecord",
]
