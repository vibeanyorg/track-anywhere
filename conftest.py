from __future__ import annotations

import os


def _configure_test_database() -> None:
    if os.getenv("TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE") != "1":
        os.environ["TRACK_ANYWHERE_DATABASE_URL"] = "sqlite:///:memory:"
    os.environ.setdefault("TRACK_ANYWHERE_FAST_TEST_SCHEMA", "1")


_configure_test_database()
