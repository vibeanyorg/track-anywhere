from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class MonthlyCategorySummaryRecord(V2Base):
    __tablename__ = "monthly_category_summaries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id", "category_id", "category_version_id"],
            [
                "category_versions.book_id",
                "category_versions.category_id",
                "category_versions.category_version_id",
            ],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["book_id", "as_of_book_position"],
            ["ledger_events.book_id", "ledger_events.book_position"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["projection_name", "projector_version", "book_id", "generation"],
            [
                "projection_generations.projection_name",
                "projection_generations.projector_version",
                "projection_generations.book_id",
                "projection_generations.generation",
            ],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "projection_name = 'monthly_category_summary'",
            name="projection_name_exact",
        ),
        CheckConstraint("projector_version > 0", name="projector_version_positive"),
        CheckConstraint("generation > 0", name="generation_positive"),
        CheckConstraint(
            "date_trunc('month', period_start)::date = period_start",
            name="period_month_start",
        ),
        CheckConstraint(
            "line_kind in ('expense','income','transfer','tax','investment')",
            name="line_kind_valid",
        ),
        CheckConstraint("units <> 0", name="units_nonzero"),
        CheckConstraint("as_of_book_position > 0", name="as_of_positive"),
        Index(
            "ix_monthly_category_summaries_book_period",
            "book_id",
            "generation",
            "period_start",
        ),
    )

    projection_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    projector_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    category_id: Mapped[UUID] = mapped_column(primary_key=True)
    category_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    line_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    units: Mapped[Decimal] = mapped_column(Numeric(48, 0))
    as_of_book_position: Mapped[int] = mapped_column(BigInteger)


__all__ = ["MonthlyCategorySummaryRecord"]
