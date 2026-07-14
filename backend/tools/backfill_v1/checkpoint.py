from __future__ import annotations

import json


def canonical_source_key(*parts: object) -> str:
    if not parts:
        raise ValueError("canonical source key requires at least one part")
    normalized: list[str | int | bool | None] = []
    for part in parts:
        if part is None or type(part) in (str, int, bool):
            normalized.append(part)
        else:
            raise TypeError("canonical source key parts must be scalar values")
    return json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    )


__all__ = ["canonical_source_key"]
