from __future__ import annotations

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .storage_models import Base


class PaymentInstrumentRecord(Base):
    __tablename__ = "payment_instruments"
    __table_args__ = (
        UniqueConstraint("book_id", "slug", name="uq_payment_instruments_book_slug"),
        Index("ix_payment_instruments_book_status", "book_id", "status"),
        Index("ix_payment_instruments_account", "account_id"),
    )

    instrument_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    slug: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    account_id: Mapped[str] = mapped_column(String(80))
    last4: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    version: Mapped[int] = mapped_column(Integer)
