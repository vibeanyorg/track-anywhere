from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import V2Base


class ProtectedDescriptionSidecarRecord(V2Base):
    __tablename__ = "protected_description_sidecars"
    __table_args__ = (
        ForeignKeyConstraint(
            ["book_id"],
            ["books.book_id"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("btrim(kind) <> ''", name="kind_nonblank"),
        CheckConstraint("btrim(algorithm) <> ''", name="algorithm_nonblank"),
        CheckConstraint("octet_length(content_hash) = 32", name="content_hash_length"),
        CheckConstraint(
            "(status = 'active' and erased_at is null "
            "and ciphertext is not null and key_ref is not null "
            "and nonce is not null) "
            "or (status = 'erased' and erased_at is not null "
            "and ciphertext is null and key_ref is null and nonce is null)",
            name="lifecycle_shape",
        ),
    )

    book_id: Mapped[UUID] = mapped_column(primary_key=True)
    sidecar_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    algorithm: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    erased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["ProtectedDescriptionSidecarRecord"]
