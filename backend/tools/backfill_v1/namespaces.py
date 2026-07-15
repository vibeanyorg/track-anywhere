from __future__ import annotations

import json
from uuid import UUID, uuid5


# Protocol constants: changing either value changes every generated V2 identity.
BACKFILL_V1_NAMESPACE = UUID("3f021172-6aa9-5b36-9208-f238bc35c596")
ENTITY_KINDS = frozenset(
    {
        "account",
        "book",
        "category",
        "category_version",
        "command",
        "counterparty",
        "event",
        "line",
        "line_version",
        "posting",
        "transaction",
    }
)


def deterministic_uuid(kind: str, *source_parts: str) -> UUID:
    if kind not in ENTITY_KINDS:
        raise ValueError(f"unknown deterministic UUID kind: {kind}")
    if not source_parts or any(
        type(part) is not str or not part for part in source_parts
    ):
        raise ValueError("deterministic UUID source parts must be nonblank strings")
    kind_namespace = uuid5(BACKFILL_V1_NAMESPACE, kind)
    encoded = json.dumps(
        list(source_parts),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(kind_namespace, encoded)


__all__ = ["BACKFILL_V1_NAMESPACE", "ENTITY_KINDS", "deterministic_uuid"]
