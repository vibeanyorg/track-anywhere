from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class InvestmentLotRecord(V2Base):
    __tablename__ = "investment_lots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "acquisition_transaction_id"],
            [
                "journal_transactions.book_id",
                "journal_transactions.transaction_id",
            ],
            name="fk_investment_lots_acquisition_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["instrument_asset_code"],
            ["assets.asset_code"],
            name="fk_investment_lots_instrument_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["settlement_asset_code"],
            ["assets.asset_code"],
            name="fk_investment_lots_settlement_asset",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_investment_lots_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_investment_lots_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "acquired_quantity_units > 0", name="acquired_quantity_positive"
        ),
        CheckConstraint("acquired_cost_units > 0", name="acquired_cost_positive"),
        CheckConstraint("fee_units is null or fee_units > 0", name="fee_positive"),
        CheckConstraint(
            "remaining_quantity_units between 0 and acquired_quantity_units",
            name="remaining_quantity_bounded",
        ),
        CheckConstraint(
            "remaining_cost_units between 0 and acquired_cost_units",
            name="remaining_cost_bounded",
        ),
        CheckConstraint(
            "(remaining_quantity_units = 0 and remaining_cost_units = 0) or "
            "(remaining_quantity_units > 0 and remaining_cost_units > 0)",
            name="remaining_state_complete",
        ),
        CheckConstraint("source_position > 0", name="source_position_positive"),
        Index(
            "ix_investment_lots_open_pool",
            "book_id",
            "instrument_asset_code",
            "settlement_asset_code",
            "source_position",
            "lot_id",
            postgresql_where=text("remaining_quantity_units > 0"),
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(primary_key=True)
    acquisition_transaction_id: Mapped[UUID] = mapped_column()
    instrument_asset_code: Mapped[str] = mapped_column(String(16))
    settlement_asset_code: Mapped[str] = mapped_column(String(16))
    acquired_quantity_units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    acquired_cost_units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    fee_units: Mapped[Decimal | None] = mapped_column(Numeric(38, 0), nullable=True)
    remaining_quantity_units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    remaining_cost_units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    source_event_id: Mapped[UUID] = mapped_column()
    source_position: Mapped[int] = mapped_column(BigInteger)


class InvestmentLotAllocationRecord(V2Base):
    __tablename__ = "investment_lot_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "lot_id"],
            ["investment_lots.book_id", "investment_lots.lot_id"],
            name="fk_investment_lot_allocations_lot",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "disposal_transaction_id"],
            [
                "journal_transactions.book_id",
                "journal_transactions.transaction_id",
            ],
            name="fk_investment_lot_allocations_disposal_transaction",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_event_id"],
            ["ledger_events.book_id", "ledger_events.event_id"],
            name="fk_investment_lot_allocations_source_event",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "source_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            name="fk_investment_lot_allocations_source_position",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "book_id",
            "disposal_transaction_id",
            "allocation_position",
            name="uq_investment_lot_allocations_disposal_position",
        ),
        UniqueConstraint(
            "book_id",
            "disposal_transaction_id",
            "lot_id",
            name="uq_investment_lot_allocations_disposal_lot",
        ),
        CheckConstraint("allocation_position >= 0", name="position_nonnegative"),
        CheckConstraint("quantity_units > 0", name="quantity_positive"),
        CheckConstraint("cost_units > 0", name="cost_positive"),
        CheckConstraint("source_position > 0", name="source_position_positive"),
        Index(
            "ix_investment_lot_allocations_disposal",
            "book_id",
            "disposal_transaction_id",
            "allocation_position",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(primary_key=True)
    lot_id: Mapped[UUID] = mapped_column()
    disposal_transaction_id: Mapped[UUID] = mapped_column()
    allocation_position: Mapped[int] = mapped_column(SmallInteger)
    quantity_units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    cost_units: Mapped[Decimal] = mapped_column(Numeric(38, 0))
    source_event_id: Mapped[UUID] = mapped_column()
    source_position: Mapped[int] = mapped_column(BigInteger)


__all__ = ["InvestmentLotAllocationRecord", "InvestmentLotRecord"]
