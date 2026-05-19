# Auth Integration

Track Anywhere uses two layers of authentication:

- **Identity login**: browser users can sign in with OAuth/OIDC. The FastAPI
  backend uses Authlib. The Django backend uses django-allauth under
  `/accounts/`.
- **RBAC authorization**: roles map to ledger scopes and default book membership.
- **Ledger command authorization**: internal credentials, idempotency, book access, and audit rules still gate every ledger command.

This keeps social login separate from the high-authority ledger command model while giving future social-app and multi-user work a stable RBAC boundary.

## Library Choice

The FastAPI backend uses Authlib instead of FastAPI Users for the first auth
slice. FastAPI Users is a good full user-management framework, but it brings an
opinionated user table, auth backend, and route stack. Track Anywhere already
has users, scoped credentials, audit logging, book memberships, and browser
CSRF handling, so the smaller integration surface is to add Authlib for
OAuth/OIDC login and map successful identities into the existing authorization
path.

The Django backend delegates login, signup, password management, and social
account flows to django-allauth. A local auth bridge maps the authenticated
Django user or allauth `SocialAccount` into the same Track Anywhere identity,
role, credential, `ta_session`, and `ta_csrf` model used by the FastAPI API
contract. That bridge is the only place where Django sessions become ledger
credentials.

## RBAC Model

The first RBAC slice uses four roles:

- `owner`: all current owner scopes, including credential management.
- `admin`: operational write scopes and user management, excluding credential issuance.
- `editor`: ledger/account/category/budget/investment/recurring write scopes, excluding user and credential management.
- `viewer`: read scopes only.

OAuth login creates or refreshes an `auth_identities` record keyed by provider + subject, creates a Track Anywhere user when needed, grants default-book membership for the selected role, and issues a short-lived internal credential for that browser session. The browser never receives that credential; it receives only the `ta_session` cookie and CSRF token.

Django group membership is the source of coarse role truth for Django sessions.
If a user's role changes, the bridge refreshes the internal credential before
the next API command is authorized.

## Environment

Local mode gets a deterministic dev session secret. Non-local OAuth login requires explicit secrets and an email allowlist until multi-user account mapping exists.

```bash
TRACK_ANYWHERE_AUTH_SESSION_SECRET=change-me
TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS=owner@example.com
TRACK_ANYWHERE_OAUTH_OWNER_EMAILS=owner@example.com
TRACK_ANYWHERE_OAUTH_DEFAULT_ROLE=viewer

TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_ID=...
TRACK_ANYWHERE_OAUTH_GOOGLE_CLIENT_SECRET=...

# Optional generic OIDC provider.
TRACK_ANYWHERE_OIDC_PROVIDER_NAME=oidc
TRACK_ANYWHERE_OIDC_CLIENT_ID=...
TRACK_ANYWHERE_OIDC_CLIENT_SECRET=...
TRACK_ANYWHERE_OIDC_METADATA_URL=https://issuer.example/.well-known/openid-configuration
TRACK_ANYWHERE_OIDC_SCOPE="openid profile email"

# Optional when running behind a proxy or tunnel.
TRACK_ANYWHERE_PUBLIC_BASE_URL=https://api.example.com
TRACK_ANYWHERE_AUTH_SUCCESS_REDIRECT=https://app.example.com/auth/complete
```

## Routes

- `GET /api/v1/auth/oauth/providers` lists configured providers.
- `GET /api/v1/auth/oauth/{provider}/authorize` starts the OAuth authorization redirect.
- `GET /api/v1/auth/oauth/{provider}/callback` exchanges the provider callback, links the identity, applies RBAC, creates a `ta_session` cookie, and returns a CSRF token in JSON or a readable `ta_csrf` cookie when redirecting.
- `GET /api/v1/auth/session` returns the current browser session identity, if present.
- `POST /api/v1/auth/logout` revokes the browser session and clears auth
  cookies. In Django it also logs out the Django session.

The browser must send `X-CSRF-Token` for mutating API calls when using `ta_session`. Bearer tokens still work for CLI and agent workflows.

## Next Step

Before opening OAuth login to arbitrary users, add invite/admin UI for role and book-membership assignment. Until that exists, non-local OAuth login requires `TRACK_ANYWHERE_OAUTH_ALLOWED_EMAILS`, and `owner` should be granted only through `TRACK_ANYWHERE_OAUTH_OWNER_EMAILS`.
