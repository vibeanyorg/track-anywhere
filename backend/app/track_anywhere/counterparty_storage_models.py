from __future__ import annotations

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .storage_models import Base


class CounterpartyRecord(Base):
    __tablename__ = "counterparties"
    __table_args__ = (
        UniqueConstraint("book_id", "slug", name="uq_counterparties_book_slug"),
        Index("ix_counterparties_book_kind_status", "book_id", "kind", "status"),
    )

    counterparty_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(80), default="book_default")
    slug: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="active")
    version: Mapped[int] = mapped_column(Integer)
