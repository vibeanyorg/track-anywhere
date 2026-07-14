"""Repository-wide pytest configuration.

Database-bearing V2 suites provision isolated PostgreSQL 17 databases through
their own fixtures.  The root hook deliberately has no implicit database
backend or process-wide connection mutation.
"""

from __future__ import annotations

import os


_DIRECT_DATABASE_STATE = (
    "TRACK_ANYWHERE_DATABASE_URL",
    "TRACK_ANYWHERE_TEST_POSTGRES_URL",
    "TRACK_ANYWHERE_FAST_TEST_SCHEMA",
)


if os.getenv("TRACK_ANYWHERE_ALLOW_EXTERNAL_TEST_DATABASE") == "1":
    for _name in _DIRECT_DATABASE_STATE:
        os.environ.pop(_name, None)
