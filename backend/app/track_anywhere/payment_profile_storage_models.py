from __future__ import annotations

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .storage_models import ASSET_CODE_LENGTH, Base


class PaymentProfileRecord(Base):
    __tablename__ = "payment_profiles"
    __table_args__ = (
        UniqueConstraint("book_id", "slug", name="uq_payment_profiles_book_slug"),
        Index("ix_payment_profiles_book_status", "book_id", "status"),
    )

    profile_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    slug: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    instrument_account_id: Mapped[str] = mapped_column(String(80))
    instrument_currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    backing_account_id: Mapped[str] = mapped_column(String(80))
    backing_currency: Mapped[str] = mapped_column(String(ASSET_CODE_LENGTH))
    settlement_mode: Mapped[str] = mapped_column(String(40))
    settlement_rate: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="active")
    version: Mapped[int] = mapped_column(Integer)
