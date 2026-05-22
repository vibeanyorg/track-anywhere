# Auth Machine Flows Design

Status: proposed
Date: 2026-05-22

## Summary

Track Anywhere should keep FastAPI as the auth authority and split auth into four explicit lanes:

1. Human browser login and admin management.
2. Interactive CLI auth through OAuth authorization code + PKCE.
3. Headless human-approved CLI auth through OAuth device authorization.
4. Non-human machine-to-machine auth through scoped API keys.

This is feasible in the current FastAPI backend. The important design constraint is that these lanes should share users, scopes, credentials, audit, and revocation, but should not share UX or threat assumptions.

Next.js should stay optional for product UI. It should not be required for login, CLI callback, device approval, API key management, or token exchange.

## Research Snapshot

The mature pattern across AI and developer CLIs is not one auth mechanism. It is a small auth portfolio with clear precedence:

| Product or standard | Pattern to copy | Source |
| --- | --- | --- |
| OAuth PKCE | Public clients use authorization code + PKCE so an intercepted code is useless without the verifier. | [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html), [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700) |
| OAuth device flow | Headless tools request a `device_code` and `user_code`, display a verification URI, then poll with the required interval and handle `authorization_pending`, `slow_down`, `expired_token`, and `access_denied`. | [RFC 8628](https://www.rfc-editor.org/rfc/rfc8628) |
| OAuth client credentials | True client credentials are for confidential clients only. A CLI is not confidential; a backend service with a stored secret can be. | [RFC 6749](https://www.rfc-editor.org/rfc/rfc6749) |
| GitHub CLI | Default is browser login stored in the system credential store, with token input or env token for headless automation. GitHub device flow is explicitly recommended for CLI/headless apps. | [gh auth login](https://cli.github.com/manual/gh_auth_login), [GitHub device flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow) |
| Claude Code | Browser login is default for humans. API key, bearer token, cloud provider credentials, helper scripts, and long-lived CI token are separate sources with documented precedence and status visibility. | [Claude Code authentication](https://code.claude.com/docs/en/authentication) |
| Gemini CLI | Offers Google OAuth for individual developers, API key for model-control or paid API use, and Vertex AI for enterprise/production workloads. | [Gemini CLI README](https://github.com/google-gemini/gemini-cli/blob/main/README.md#authentication-options) |
| Codex CLI | First run prompts sign-in, with ChatGPT account or API key as auth choices. | [Codex CLI](https://developers.openai.com/codex/cli) |
| Google Cloud CLI | Distinguishes user CLI credentials, application-default credentials, attached service accounts, and service account key import. It also supports no-browser login paths. | [ADC docs](https://cloud.google.com/docs/authentication/application-default-credentials), [gcloud auth login](https://cloud.google.com/sdk/gcloud/reference/auth/login) |

Working conclusion: Track Anywhere should make `ta auth login` human-first and PKCE-first, add `ta auth login --device` for SSH/container use, and keep API keys for automation. The CLI should always show which credential source is active.

## Current Implementation Snapshot

Current code already has most of the base primitives:

| Area | Current file | Current behavior |
| --- | --- | --- |
| Browser login UI | `backend/app/track_anywhere/api_routers/auth_pages.py` | FastAPI serves login, signup, session page, and CLI callback approval HTML. |
| Browser session API | `backend/app/track_anywhere/api_routers/auth.py` | Password login, provider OAuth login, logout, session status, API-key-backed browser session. |
| Platform OAuth server | `backend/app/track_anywhere/api_routers/oauth.py` | Metadata, client registration, authorize, token exchange, revoke. |
| PKCE exchange | `backend/app/track_anywhere/platform_auth.py` | Authorization code + PKCE for public clients, in-memory code store, one-hour access token. |
| API key credentials | `backend/app/track_anywhere/service_credentials.py` | Human `credential:write` actors can issue/list/revoke agent credentials. Agent credentials cannot issue more credentials. |
| CLI login | `cli/track_anywhere_cli/click_auth.py` and `cli/track_anywhere_cli/oauth_login.py` | Browser login with manual callback paste, direct token import, dev token, status. Token store prefers keyring and falls back to `0600` file. |

Gaps:

- No device authorization grant.
- Authorization codes are in memory, so they disappear on restart and do not work cleanly under multi-worker deployment.
- M2M credentials are named `agent`, have only minimal metadata, and max TTL is 24 hours. That is good for short-lived agent tokens, but not enough for durable machine identities.
- `auth.status` reports source but not auth kind, scopes, expiry, or credential identity.
- OAuth metadata currently advertises only `authorization_code`.

## Design Goals

- Keep FastAPI as the only auth authority.
- Preserve separation between browser sessions and bearer/API-key automation.
- Make the default CLI path safe for a human on a laptop.
- Make SSH/container/server bootstrap possible without copying passwords.
- Make automation explicit, scoped, revocable, auditable, and visible.
- Keep tokens out of URLs, logs, audit details, and persisted idempotency receipts.
- Do not introduce a new dependency just to implement device flow; existing service objects can cover it.

## Auth Lane 1: Human Browser Login and Admin

Purpose:

- User account login.
- Admin/API key management.
- Device-code approval.
- CLI PKCE approval.
- Future backoffice views.

Primary surface:

- `GET /api/v1/auth/login`
- `GET /api/v1/auth/signup`
- `GET /api/v1/auth/session-view`
- `POST /api/v1/auth/password/login/form`
- `POST /api/v1/auth/password/signup/form`
- `GET /api/v1/auth/oauth/{provider}/authorize`
- `GET /api/v1/auth/oauth/{provider}/callback`

Security model:

- Browser uses `ta_session` and `ta_csrf`.
- Mutating browser-backed routes require CSRF.
- Browser session maps to an internal credential reference, but the raw credential is not exposed to the browser.
- Non-local password signup stays allowlist-gated.

Admin additions:

- Add a small FastAPI HTML/API surface for machine credentials:
  - List active/revoked machine credentials.
  - Create credential with name, purpose, scopes, TTL, optional book binding.
  - Rotate credential.
  - Revoke credential by id.
  - Show raw secret once on creation.

## Auth Lane 2: PKCE for Interactive CLI

Purpose:

- Best default for `ta auth login` on a developer laptop.
- User is present and has a browser on the same or reachable machine.

Current shape:

- CLI creates `state`, `code_verifier`, and `code_challenge`.
- CLI opens `/api/v1/auth/callback?...`.
- User logs in and approves in FastAPI.
- FastAPI returns a callback URL containing an authorization code.
- CLI exchanges code + verifier at `/api/v1/oauth/token`.

Recommended changes:

- Keep PKCE as the default.
- Prefer loopback callback auto-capture later, but keep manual paste as fallback.
- Persist authorization codes in storage instead of only `PlatformKeyExchange._codes`.
- Keep `S256` only. Do not support `plain`.
- Keep access token TTL short, currently one hour.
- Add `auth.status` fields:
  - `auth_kind: pkce`
  - `credential_id`
  - `scopes`
  - `expires_at`
  - `base_url`
  - `token_source`

Token response stays OAuth-compatible:

```json
{
  "access_token": "ta_...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "account:read book:read ledger:read"
}
```

Refresh tokens:

- Do not add refresh tokens in the first implementation.
- Requiring re-login after expiry is acceptable for early-stage personal finance usage.
- Revisit refresh tokens only when token expiry becomes painful for long-running interactive sessions.

## Auth Lane 3: Device Flow for Headless CLI

Purpose:

- SSH sessions.
- Containers.
- Remote dev boxes.
- Terminals where the browser cannot receive a local callback.
- AI agents running in an interactive terminal where a human can approve from another browser.

CLI UX:

```bash
ta auth login --device
```

Human output:

```text
Open this URL on any device:
  https://track-anywhere.example.com/api/v1/auth/device

Enter code:
  7KQF-P9TR

Waiting for approval...
```

JSON output should include polling metadata but never include an access token until auth succeeds:

```json
{
  "verification_uri": "https://track-anywhere.example.com/api/v1/auth/device",
  "verification_uri_complete": "https://track-anywhere.example.com/api/v1/auth/device?user_code=7KQF-P9TR",
  "user_code": "7KQF-P9TR",
  "expires_in": 900,
  "interval": 5
}
```

New API endpoints:

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `POST /api/v1/oauth/device/authorize` | Public client | Create `device_code`, `user_code`, `verification_uri`, `expires_in`, `interval`. |
| `GET /api/v1/auth/device` | Browser session or login redirect | Show user-code entry/approval page. |
| `POST /api/v1/auth/device` | Browser session + CSRF | Approve or deny the pending device grant. |
| `POST /api/v1/oauth/token` | Public client | Add device grant handling when `grant_type=urn:ietf:params:oauth:grant-type:device_code`. |

Device grant state machine:

| State | Meaning |
| --- | --- |
| `pending` | CLI has started the grant; no human approval yet. |
| `approved` | Browser session approved; next valid poll can mint a token. |
| `denied` | Browser session denied; token endpoint returns `access_denied`. |
| `consumed` | Token was minted; further polls return invalid/expired grant. |
| `expired` | `expires_at` passed. |

Token endpoint errors:

- `authorization_pending`: user has not approved yet.
- `slow_down`: client polled faster than `interval`; increase interval by 5 seconds.
- `expired_token`: grant expired.
- `access_denied`: user denied.
- `invalid_grant`: malformed, unknown, consumed, or mismatched device code.

Device storage:

Add `oauth_device_grants`:

| Column | Notes |
| --- | --- |
| `device_code_hash` | Primary lookup; never store raw `device_code`. |
| `user_code_hash` | Browser lookup; normalize before hashing. |
| `client_id` | Must match the token request. |
| `scope` | Space-separated canonical scope string or JSON list. |
| `resource` | Optional resource indicator. |
| `status` | `pending`, `approved`, `denied`, `consumed`, `expired`. |
| `expires_at` | Short, e.g. 15 minutes. |
| `interval_seconds` | Start at 5. |
| `last_poll_at` | Enforce polling interval. |
| `poll_count` | Audit/rate-limit signal. |
| `approved_by_actor_id` | Set on approval. |
| `approved_at` | Set on approval. |
| `created_at` | Audit. |

Rate limiting:

- Rate-limit user-code entry by IP and normalized user code.
- Rate-limit polling by `device_code_hash`.
- Use constant-ish error messages for unknown vs expired user codes on the browser form.

## Auth Lane 4: M2M API Keys

Purpose:

- Scripts.
- Scheduled jobs.
- MCP servers.
- Remote AI workers.
- CI tasks.
- Integrations where no human is present at runtime.

This should not be modeled as a public OAuth CLI flow. It is closer to API key or service-account auth. True OAuth client credentials can be added later only for confidential clients with registered client secrets.

Current route:

- `POST /api/v1/credentials/agent`
- `GET /api/v1/credentials`
- `POST /api/v1/credentials/revoke`
- `POST /api/v1/credentials/{credential_id}/revoke`

Recommended model:

- Rename product language from "agent credential" to "machine credential".
- Keep route compatibility, but add new aliases:
  - `POST /api/v1/credentials/machine`
  - `GET /api/v1/credentials/machine`
- Store metadata:
  - `name`
  - `description`
  - `credential_type`: `agent`, `mcp`, `ci`, `integration`
  - `created_by_actor_id`
  - `last_used_at`
  - `last_used_ip_hash`
  - `last_used_user_agent`
  - `rotated_from_jti`
  - `key_prefix`
- Keep raw token one-time only.
- Hash token at rest.
- Show prefix and credential id in listings.
- Default TTL should differ by use:
  - Short-lived agent handoff: 30 minutes to 24 hours.
  - Durable machine key: 30 days default, 90 days max.
  - CI one-off: 24 hours max.

Credential format:

```text
ta_m2m_<public-prefix>_<secret>
```

The prefix gives operators something safe to search and revoke. The secret remains random and hashed.

Request auth:

- Continue accepting `Authorization: Bearer <token>`.
- Continue accepting `X-API-Key: <token>` for API-key integrations.
- CLI token import stays:

```bash
ta auth login <token>
```

But status should label it:

```text
Authenticated with machine API key ta_m2m_ab12...
Scopes: ledger:read, capture:draft
Expires: 2026-06-21T...
```

## Credential Source Precedence

Keep the current bias: humans should use OS keyring; env tokens are automation-only.

Recommended precedence:

1. Explicit `--token`.
2. `TRACK_ANYWHERE_TOKEN`, only with `--insecure-automation`.
3. Stored keyring/file token.
4. Browser session cookies, browser only.
5. Local dev token, local mode only.

`ta auth status --json` should report:

```json
{
  "authenticated": true,
  "base_url": "http://127.0.0.1:12306",
  "token_source": "keyring",
  "auth_kind": "pkce",
  "credential_id": "....",
  "actor_type": "human",
  "scopes": ["account:read", "book:read", "ledger:read"],
  "expires_at": "2026-05-22T10:00:00+00:00"
}
```

## Shared Token and Scope Rules

- All non-browser access should still pass through `service.actor_from_token`.
- `credential:write` must remain human-only.
- Platform OAuth tokens and device-flow tokens must never mint `credential:write`.
- M2M credentials must be book-scoped as soon as book-level permissions become first-class in auth management UI.
- Default CLI scope remains narrow: `account:read book:read ledger:read`.
- Write scopes should be explicit in `ta auth login --scope ...`, device auth, and M2M issuance.

## OAuth Metadata Changes

After device flow lands, `/api/v1/oauth/authorization-server` should advertise:

```json
{
  "grant_types_supported": [
    "authorization_code",
    "urn:ietf:params:oauth:grant-type:device_code"
  ],
  "device_authorization_endpoint": "https://.../api/v1/oauth/device/authorize",
  "code_challenge_methods_supported": ["S256"]
}
```

Do not advertise client credentials until the backend supports confidential OAuth clients with client secret rotation.

## Implementation Plan

Phase 1: contract cleanup, no behavior break

- Update docs and CLI help language to name the four lanes.
- Add `auth_kind`, scopes, expiry, and credential id to auth status responses where available.
- Keep existing endpoints compatible.

Phase 2: persistence and metadata

- Persist authorization codes.
- Add machine credential metadata columns.
- Add `last_used_at` updates on successful token verification.
- Add public key prefix to credential listing.

Phase 3: device flow

- Add `DeviceAuthorizeCommand` and `DeviceTokenCommand`.
- Add device-grant storage and state machine.
- Add browser device approval pages in FastAPI.
- Extend `/api/v1/oauth/token`.
- Add `ta auth login --device`.
- Add JSON and human output tests.

Phase 4: M2M hardening

- Add `/credentials/machine` alias.
- Split TTL policy by credential type.
- Add rotation endpoint.
- Add UI/API revocation by id.
- Add audit entries for issue, rotate, revoke, and first/last use.

Phase 5: optional refresh tokens

- Only if one-hour interactive tokens are too painful.
- Refresh tokens must be hashed, rotatable, revocable, and bound to client id.
- Device and PKCE refresh tokens should remain human-user credentials, not M2M credentials.

## Test Plan

- API tests:
  - PKCE requires `S256`.
  - Authorization code is single-use and persisted.
  - Device authorize returns `device_code`, `user_code`, `verification_uri`, `expires_in`, `interval`.
  - Polling too early returns `slow_down`.
  - Pending grant returns `authorization_pending`.
  - Denied grant returns `access_denied`.
  - Expired grant returns `expired_token`.
  - Approved grant mints a scoped credential once.
  - M2M list never returns raw token.
  - API keys cannot issue API keys.
- CLI tests:
  - `ta auth login` keeps PKCE default.
  - `ta auth login --device --json` emits stable machine-readable progress.
  - `ta auth status --json` includes auth kind, source, scopes, and expiry.
  - `TRACK_ANYWHERE_TOKEN` still requires `--insecure-automation`.
- Structure tests:
  - Keep all new backend and CLI files under the 300-line limit enforced by `backend/tests/test_structure.py`.

## Decisions

- FastAPI owns auth pages and auth APIs.
- Next.js is optional and not required for login or machine auth.
- PKCE is default for interactive CLI.
- Device flow is the right headless human-approved flow.
- API keys are the right first M2M mechanism.
- OAuth client credentials should wait until there is a real confidential-client registry.
- Refresh tokens are postponed.

## Open Questions

- Should durable M2M keys default to 30 days or require explicit TTL every time?
- Do we need named service accounts before the multi-user/backoffice UI is ready?
- Should device approval allow scope downgrading at approval time?
- Should `ta auth login --device` be automatic when `--no-browser` is passed, or remain an explicit flag?
- Should future OAuth refresh tokens be disabled for device flow in production unless MFA is available?
