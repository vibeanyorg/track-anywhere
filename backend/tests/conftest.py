from __future__ import annotations

import os


_DIRECT_TEST_DATABASE_STATE = (
    "TRACK_ANYWHERE_DATABASE_URL",
    "TRACK_ANYWHERE_TEST_POSTGRES_URL",
    "TRACK_ANYWHERE_FAST_TEST_SCHEMA",
)


if os.getenv("TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE") == "1":
    for name in _DIRECT_TEST_DATABASE_STATE:
        os.environ.pop(name, None)
else:
    os.environ.setdefault("TRACK_ANYWHERE_DATABASE_URL", "sqlite:///:memory:")
