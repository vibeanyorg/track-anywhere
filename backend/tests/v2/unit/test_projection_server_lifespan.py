from __future__ import annotations

from threading import Event

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from track_anywhere.server import ProtocolApplication, _projection_poll_seconds


class _ProjectionRuntime:
    def __init__(self) -> None:
        self.started = Event()
        self.stopped = Event()

    async def run_forever(self) -> None:
        self.started.set()
        try:
            await anyio.sleep_forever()
        finally:
            self.stopped.set()


def test_protocol_lifespan_starts_and_stops_embedded_projection_runtime() -> None:
    runtime = _ProjectionRuntime()
    application = ProtocolApplication(
        rest_application=FastAPI(),
        discovery_application=FastAPI(),
        mcp_runtime=None,
        projection_runtime=runtime,
    )

    with TestClient(application):
        assert runtime.started.wait(timeout=1)

    assert runtime.stopped.wait(timeout=1)


@pytest.mark.parametrize("value", ["", "0", "nan", "301", "not-a-number"])
def test_projection_poll_interval_environment_is_fail_closed(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACK_ANYWHERE_PROJECTION_POLL_SECONDS", value)

    with pytest.raises(ValueError, match="PROJECTION_POLL_SECONDS"):
        _projection_poll_seconds()


def test_projection_poll_interval_defaults_to_two_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACK_ANYWHERE_PROJECTION_POLL_SECONDS", raising=False)

    assert _projection_poll_seconds() == 2.0
