# Backend Agent Instructions

These instructions apply to every file under `backend/`.

This subtree contains the FastAPI backend for Track Anywhere. Treat it as a
versioned API boundary around a finance domain, not as a place for quick HTTP
scripts. Keep changes small, behavior-preserving where possible, and covered by
tests.

## Project Shape

- Runtime target: Python 3.12-3.13.
- Framework stack: FastAPI 0.115+, Pydantic v2, SQLAlchemy 2.x, Alembic.
- Application import path: `backend/app`.
- FastAPI entrypoint: `backend/app/main.py`, which imports `track_anywhere.api:app`.
- Backend tests live in `backend/tests`.
- Public routes are under `/api/v2`; do not introduce unversioned public API
  routes.

## FastAPI Practices

- Keep path operation functions thin. They may parse HTTP inputs, call
  dependencies, delegate to application services, serialize responses, and map
  domain errors to HTTP responses. They must not contain domain rules.
- Prefer `APIRouter` modules for new resource groups or substantial new API
  surface. Include routers from a single application composition point instead
  of growing one large route file indefinitely.
- Use dependency injection for request-scoped concerns: authentication,
  authorization context, idempotency keys, database/session handles, and request
  guards. Prefer reusable `typing.Annotated[...]` aliases for repeated
  dependencies.
- Use `response_model` or explicit Pydantic response schemas for new public
  endpoints so FastAPI validates, documents, and filters output. Avoid returning
  arbitrary internal objects directly.
- Use Pydantic models for request bodies. Avoid accepting raw `dict[str, Any]`
  in new endpoints unless the endpoint is intentionally schema-less and that
  decision is documented in a nearby comment or test.
- Raise `HTTPException` only at the HTTP boundary or inside HTTP dependencies.
  Domain and service layers should raise domain exceptions that the API layer
  maps to status codes.
- Prefer FastAPI `lifespan` for application startup and cleanup. Do not add new
  `startup` or `shutdown` event handlers.
- Choose `async def` only when the handler or dependency awaits async work.
  Synchronous CPU-bound or blocking service calls should remain `def` unless
  the underlying stack is made async end to end.
- Do not leak secrets, bearer tokens, raw credentials, stack traces, or raw
  request payloads in JSON responses, logs, audit details, or validation errors.
- Mutating endpoints must preserve the existing idempotency-key pattern unless a
  product requirement explicitly says otherwise.
- Preserve and update OpenAPI behavior intentionally. Any breaking contract
  change must update contract/snapshot tests and document the reason.

## Python Practices

- Use modern typed Python: `from __future__ import annotations`, `|` unions,
  precise collection types, and explicit return annotations on public functions.
- Prefer small pure functions and dataclasses/Pydantic models over mutable
  ad-hoc dictionaries for internal data.
- Represent V2 money as exact integer units paired with an explicit asset
  scale. Parse decimal text at the boundary without rounding, then keep integer
  units through commands, events, projections, and persistence. Never use
  `float` or persist `Decimal` values for ledger facts.
- Use timezone-aware `datetime` values for persisted or API-visible timestamps.
  Do not silently strip offsets.
- Keep imports explicit and local to their layer. Avoid wildcard imports,
  import-time side effects, and module-level work that makes tests order
  dependent.
- Use structured parsing/validation APIs instead of hand-rolled string parsing
  when a standard library, Pydantic, SQLAlchemy, or an existing local helper can
  do the job.
- Do not add dependencies without an explicit request. Reuse the standard
  library and existing project helpers first.
- Prefer clear names over comments. Add comments only for non-obvious
  invariants, security constraints, or domain rules.

## Clean Code Rules

- Optimize for readable intent, not cleverness. A future agent should be able
  to see the business rule and the failure mode without reconstructing hidden
  state.
- Keep functions focused. Split code when a function mixes validation,
  authorization, persistence, serialization, and formatting.
- Remove duplication when it encodes the same rule in multiple places. Do not
  abstract code merely because two blocks look visually similar.
- Prefer deletion over addition. Before adding a helper, check whether an
  existing command model, service method, domain object, or serializer already
  owns the concept.
- Keep error messages stable, specific, and testable. Do not return vague
  strings such as `"error"` or `"invalid request"` for domain failures.
- Avoid boolean flag parameters that select unrelated behavior. Use separate
  functions or explicit command objects when behavior branches materially.
- Do not hide broad exceptions. Catch narrow domain/framework exceptions,
  preserve causal context with `from exc` where useful, and let unexpected
  defects fail loudly in tests.

## Clean Architecture Boundaries

- API layer (`track_anywhere.api` and future router/dependency modules):
  FastAPI imports are allowed here. This layer owns HTTP parameters, cookies,
  headers, status codes, dependency wiring, response models, and exception
  mapping.
- Application layer (`track_anywhere.application`): owns commands, Book-scoped
  authorization, idempotency, unit-of-work boundaries, and ledger commits. It
  must not import FastAPI.
- API schema layer (`track_anywhere.api.v2.schemas`): owns typed HTTP inputs and
  actor extraction. Keep transport validation out of the domain.
- Domain layer (`track_anywhere.domain`): owns immutable event contracts,
  journal, money, reporting, investment, and privacy invariants. It must not
  depend on HTTP, ORM models, cookies, or test clients.
- Infrastructure layer (`track_anywhere.infrastructure` and `alembic`): owns
  PostgreSQL mappings, event storage, repositories, projections, and migrations.
  ORM details must not leak into domain contracts.
- Dependencies point inward: API -> application -> domain, with infrastructure
  implementing application ports. Domain code never calls API code.

## Testing And Verification

- Add or update backend tests for every behavior change. Prefer focused tests
  near the layer being changed, plus API tests when the HTTP contract changes.
- Use FastAPI `TestClient` for HTTP-level tests unless the app is intentionally
  moved to a fully async test stack.
- PostgreSQL 17 is mandatory for every database-bearing persistence, migration,
  repository, concurrency, replay, and backfill test. Pure unit tests must not
  silently install a database fallback.
- Test security-sensitive behavior directly: auth failures, CSRF/origin checks,
  idempotency conflicts, stale versions, and sensitive-data redaction.
- For API contract changes, update
  `backend/tests/snapshots/public-api-v2.json` only when the new contract is
  intentional.
- Run `uv run pytest backend/tests` after backend changes. If the change also
  affects CLI behavior or package wiring, run the full `uv run pytest`.
- If linting or typechecking tools are added to the project later, run the
  relevant configured checks before claiming completion.

## Migration Rules

- V2 Alembic migrations start from a clean PostgreSQL 17 schema. Do not add an
  in-place V1-to-V2 compatibility path; V1 data moves through the separately
  verified backfill workflow.
- Alembic migrations must be deterministic, reviewable, and reversible when the
  database operation allows it.
- Do not modify an existing migration that may already have been applied unless
  the repository history clearly shows it is still unreleased and safe to edit.
- Keep schema changes and application behavior changes in the same task only
  when tests prove the transition path.

## Review Checklist

Before finishing backend work, verify:

- HTTP boundary is thin and uses FastAPI features deliberately.
- Domain rules are not embedded in route handlers.
- New public inputs and outputs are typed and documented through Pydantic or
  FastAPI metadata.
- Money uses exact integer units and explicit asset scales; timestamps are
  timezone-aware when API-visible.
- Every changed V2 database path passes its PostgreSQL 17 integration gate.
- Security, idempotency, and API contract tests are updated when touched.
- `uv run pytest backend/tests` passes or the remaining failure is reported with
  exact evidence.
