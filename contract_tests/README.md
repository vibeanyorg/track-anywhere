# V2 contract conformance tests

These tests exercise the public V2 HTTP contract and the supported CLI
transport against an isolated PostgreSQL 17 database.

Current scope:

- health/readiness and the reviewed V2 OpenAPI route snapshot;
- catalog creation plus journal post/query/classify/reverse behavior;
- exact decimal-string and explicit idempotency-key transport;
- API-key browser-session exchange and token status;
- supported CLI handlers through the same request/response adapter used by
  `ta`.

Rules:

- the fixture creates a fresh V2 database through `PostgresDatabaseFactory`;
- it installs the database's runtime-role URL before importing the app/client;
- there is no SQLite or in-memory database fallback;
- tests use `BackendApiClient` rather than importing route handlers;
- assertions cover observable contract behavior, not framework internals.
