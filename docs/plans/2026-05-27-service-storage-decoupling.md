# Service/Storage Decoupling Plan

Status: active goal
Date: 2026-05-27
Scope: Track Anywhere backend service/storage architecture

## Goal

Make the backend write architecture high cohesion and low coupling without
adding speculative framework layers. Small features should normally touch one
use-case module, one command/API surface when needed, and one focused repository
method. They should not require editing a central service object and a wide
storage facade for every minor behavior change.

## Design Rules

1. Storage write methods must not accept `FinanceService`.
   They receive explicit records, dirty lists, or small change-set objects.
2. API routers must not touch `service.storage` directly except system health
   endpoints that intentionally inspect database readiness.
3. Domain objects do not know storage, FastAPI, HTTP, cookies, or sessions.
4. Use cases own authorization, command validation, idempotency, audit intent,
   and domain orchestration.
5. Repositories own mapping and persistence only. They do not pull hidden dirty
   state from arbitrary containers.
6. Fail fast on unsupported states. Do not add fallback branches that silently
   accept legacy shapes.
7. Prefer a small explicit interface over generic helpers that can write
   anything.

## Current Coupling To Remove

- `FinanceService` is still both composition root and mutable state container.
- `OrmStorage.load_into(service)` hydrates many service-owned mirrors.
- Partial storage writers accept the entire service and read dirty state from
  credentials, audit, idempotency, assets, categories, and payment containers.
- Some API routers call `service.storage` directly.
- Some domain directories expose private mutation helpers that services call.

## Target Shape

```text
API router
  -> use case method
     -> command model
     -> domain aggregate / policy
     -> UnitOfWork
        -> focused repositories
           -> SQLAlchemy mappings
```

Read paths should use explicit query services or read repositories. Process
caches are optional optimizations and must not change semantics.

## Refactor Sequence

1. Baseline current write architecture tests and performance benchmark.
2. Add architecture guardrails:
   - no storage write method accepts `service`;
   - no API router uses `service.storage`, except system readiness;
   - no new private domain helper calls from service modules.
3. Replace storage write signatures with explicit change-set inputs.
4. Move API direct storage access behind service/query methods.
5. Split OAuth grant persistence from protocol state machine.
6. Reduce `FinanceService` to composition/facade by moving use-case
   dependencies to explicit constructors.
7. Re-run full backend, CLI, e2e Docker Postgres, stable smoke, and write
   performance benchmark.

## Acceptance Criteria

- All tests pass.
- Stable smoke passes locally and on cc6 when deployment is requested.
- `record_transaction` write performance stays within the existing incremental
  budget and remains materially faster than the legacy full-state benchmark.
- Storage write methods have explicit inputs and do not depend on service
  internals.
- API routers call service/query methods rather than storage directly.
- No legacy full snapshot persistence or fallback write path remains.

## God-Class Remediation Follow-Up

The next cleanup pass must target classes with multiple real reasons to change,
not cosmetic file moves.

### Hotspots

- `ServiceBootstrapMixin` combines snapshot hydration, owner credential
  bootstrap, and startup data foundation/migration checks.
- `CatalogRepository` combines unrelated write responsibilities for books,
  users, categories, payment objects, drafts, recurring items, budgets,
  investments, attachments, and reconciliation actions.
- `OrmStorage` is still a broad storage facade. It can remain a temporary
  composition point only if the unit-of-work and repositories beneath it are
  focused.

### Required Changes

1. Split service bootstrap into focused classes:
   - state hydration from `StorageSnapshot`;
   - owner credential bootstrap;
   - domain foundation repair/validation.
2. Replace `CatalogRepository` with focused repositories exposed directly from
   `StorageUnitOfWork`.
3. Add architecture tests that forbid `ServiceBootstrapMixin`,
   `CatalogRepository`, and `uow.catalog` from returning.
4. Keep behavior unchanged and rerun full tests, Docker Postgres E2E, and the
   write benchmark.
