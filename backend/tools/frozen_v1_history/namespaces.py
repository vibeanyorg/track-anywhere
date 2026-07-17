from __future__ import annotations

import json
from uuid import UUID, uuid5

from .constants import FROZEN_UUID_NAMESPACE


ENTITY_KINDS = frozenset(
    {
        "account",
        "archive",
        "book",
        "category",
        "category_version",
        "command",
        "counterparty",
        "description",
        "event",
        "line",
        "line_version",
        "posting",
        "reporting",
        "transaction",
    }
)


def deterministic_uuid(kind: str, *source_parts: str) -> UUID:
    if kind not in ENTITY_KINDS:
        raise ValueError("unknown deterministic UUID kind")
    if not source_parts or any(
        type(part) is not str or not part.strip() for part in source_parts
    ):
        raise ValueError("deterministic UUID source parts must be nonblank strings")
    kind_namespace = uuid5(FROZEN_UUID_NAMESPACE, kind)
    encoded_parts = json.dumps(
        list(source_parts),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(kind_namespace, encoded_parts)


__all__ = ["ENTITY_KINDS", "deterministic_uuid"]
