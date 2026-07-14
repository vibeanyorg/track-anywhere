from __future__ import annotations

from typing import Any

from .presenter_base import Presenter, object_summary


def generic_payload_panel(title: str) -> Presenter:
    def render(data: Any):
        payload = data if isinstance(data, dict) else {"value": data}
        return object_summary(
            title,
            [(str(key), value) for key, value in payload.items()],
        )

    return render
