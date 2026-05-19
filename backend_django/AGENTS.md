# Django Backend Agent Instructions

These instructions apply to every file under `backend_django/`.

This subtree contains the Django implementation of Track Anywhere. Prefer
mature Django ecosystem features over custom framework code whenever they fit
the problem.

## API Surface Boundaries

- Use **Django Ninja** for the stable product/agent/CLI API under `/api/v1/`.
  This surface should preserve the existing command-style contract: typed
  request bodies, idempotency keys for mutations, bearer/session auth, JSON
  response shapes, and OpenAPI compatibility.
- Use **Django REST Framework** for backoffice/resource APIs under
  `/api/v1/backoffice/`. This surface is for admin-adjacent lists, detail views,
  filtering, search, ordering, permissions, and internal tools.
- Use **Django Admin** for human operations: inspection, staff workflows,
  emergency data review, and permission/user management.
- Use **Django auth + django-allauth** for account identity, password storage,
  account management, and social account integration. In the separated product
  UI, Next.js owns login/signup screens and calls JSON auth endpoints under
  `/api/v1/auth/`; do not send app users directly to `/accounts/` for the main
  login flow. `/accounts/` remains allauth-owned for provider callbacks and
  lower-level account management.
- Use **Django auth groups/permissions** for coarse roles and
  **django-guardian** for object-level book permissions.

## Rules

- Do not implement the same write workflow in both Ninja and DRF. Business
  command writes belong in Ninja unless there is a specific backoffice-only
  management requirement.
- Keep DRF backoffice endpoints read-only by default. If a DRF endpoint mutates
  data, document why it is not part of the Ninja command API and cover it with
  permission tests.
- Do not duplicate authorization logic separately in Ninja and DRF. Put shared
  role/object-permission decisions in local Django auth helpers and call them
  from both surfaces.
- Do not add custom login, OAuth, role, or object-permission frameworks while
  Django/allauth/guardian can cover the requirement.
- Keep URL ownership clear:
  - `/api/v1/` is Ninja.
  - `/api/v1/backoffice/` is DRF.
  - `/admin/` is Django Admin.
  - `/accounts/` is allauth and should not be used as the primary separated
    frontend login UI.
- If changing public `/api/v1` behavior, update the route/contract tests that
  compare the Django backend with the existing API snapshot.
