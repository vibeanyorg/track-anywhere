"""Backend pytest boundary; database fixtures live under ``tests/v2``."""

from __future__ import annotations

import os


if os.getenv("TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE") == "1":
    for _name in (
        "TRACK_ANYWHERE_DATABASE_URL",
        "TRACK_ANYWHERE_TEST_POSTGRES_URL",
        "TRACK_ANYWHERE_FAST_TEST_SCHEMA",
    ):
        os.environ.pop(_name, None)
