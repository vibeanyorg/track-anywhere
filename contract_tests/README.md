# Contract Conformance Tests

These tests prove that backend implementations can differ internally while
preserving the same external behavior.

Current scope:

- `/api/v1` HTTP behavior for FastAPI and Django.
- CLI command behavior for FastAPI and Django through the same command handler
  layer used by `ta`.
- Route/method contract against the public API snapshot.
- Auth, logout, session cookies, idempotency, validation errors, and core
  ledger flows.

Planned extension points:

- MCP conformance: snapshot tool schemas and run the same golden workflows
  through each MCP adapter once MCP exists in this repository.

Rules:

- Tests in this directory must not import route handlers directly.
- Tests should use the `BackendApiClient` interface from `api_clients.py`.
- CLI tests should use `requester_for_backend` so command handlers exercise the
  selected backend through the same request/response contract.
- Assertions should compare observable contract behavior, not implementation
  internals such as generated IDs or framework-specific exception classes.
