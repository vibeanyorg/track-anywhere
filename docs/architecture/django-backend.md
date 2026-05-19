# Django Backend

`backend_django/` is the Django version of the Track Anywhere backend. It keeps
the existing `/api/v1` contract for CLI/agent clients while adding the mature
Django ecosystem around identity, admin, RBAC, and backoffice APIs.

The migration rule is: keep outer contracts stable, replace infrastructure with
Django primitives where Django already has a mature answer.

- **Django Admin**: internal backoffice surface.
- **Django auth / groups / permissions**: account identity and coarse roles.
- **django-allauth**: email login and social account integration.
- **django-guardian**: book-level object permissions.
- **DRF**: backoffice REST resources with filtering/search/order support.
- **Django Ninja**: compatibility API for the existing `/api/v1` CLI/agent
  contract and Pydantic command schemas.

Run it locally:

```bash
uv run python backend_django/manage.py runserver 8001
```

Then authenticate the same way as the FastAPI server:

```bash
TRACK_ANYWHERE_API=http://localhost:8001 TRACK_ANYWHERE_WEB_URL=http://127.0.0.1:3000 track-anywhere auth login
```

For non-interactive automation, `track-anywhere auth login <token>` still stores
an existing API token directly.

Run Django ecosystem migrations:

```bash
uv run python backend_django/manage.py migrate --run-syncdb
```

Run backend conformance checks:

```bash
uv run pytest contract_tests -q
```

These tests run the same `/api/v1` and CLI workflows against the FastAPI and
Django implementations.

## Current Shape

- Django project and ASGI/WSGI entrypoints under `backend_django/config`.
- Django Ninja API compatibility adapter under
  `backend_django/track_anywhere_django/api.py`.
- DRF router under `/api/v1/backoffice/`.
- allauth routes under `/accounts/`.
- Core admin models are registered as read-only previews over existing ledger
  tables.
- Role groups are bootstrapped after migrations:
  `Track Anywhere Owners`, `Admins`, `Editors`, and `Viewers`.
- Book object permissions are assigned with django-guardian and used to filter
  backoffice books/accounts for non-staff users.

## Intentional limits

- Ledger write rules still run through the existing service/command layer, so
  balances, idempotency, and audit behavior stay identical while the Django
  version grows.
- The ledger persistence model is still SQLAlchemy/Alembic. Django ORM models for
  ledger tables are read-only previews until a dedicated data migration moves the
  source of truth.
- allauth provides the social-login surface. Provider credentials should be
  configured through Django settings/admin instead of the old FastAPI Authlib
  callback path. After allauth login, the auth bridge issues the same
  `ta_session`/`ta_csrf` cookies and internal ledger credential used by the
  FastAPI contract.
- Django user groups remain the coarse role source. The bridge refreshes the
  ledger credential when group membership changes, so RBAC decisions still run
  through the existing service layer.
- MCP is not implemented in this repo yet; when it is added, it should call the
  same service/command layer as the HTTP and CLI layers.
