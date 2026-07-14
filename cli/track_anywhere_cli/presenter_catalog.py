from __future__ import annotations

from typing import Any

from .presenter_base import object_summary


def catalog_payload(data: Any):
    payload = data if isinstance(data, dict) else {"value": data}
    return object_summary(
        "V2 catalog response",
        [(str(key), value) for key, value in payload.items()],
    )
