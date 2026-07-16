from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field, StrictBytes, StrictStr

from ...domain.privacy import FrozenContract


ProtectedContentKind: TypeAlias = Literal[
    "transaction_description",
    "import_archive",
]


class TransactionDescription(FrozenContract):
    purpose: StrictStr | None = Field(repr=False)
    transaction_memo: StrictStr | None = Field(repr=False)
    line_memos: tuple[StrictStr | None, ...] = Field(repr=False)


class ProtectedContentEnvelope(FrozenContract):
    kind: ProtectedContentKind
    canonical_plaintext: StrictBytes = Field(repr=False)


__all__ = [
    "ProtectedContentEnvelope",
    "ProtectedContentKind",
    "TransactionDescription",
]
