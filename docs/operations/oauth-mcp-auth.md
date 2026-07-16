# OAuth, MCP, and CLI authentication

Track Anywhere has three deliberately separate credential lanes. They do not
fall back to one another.

| Client | Protocol | Credential sent | Resource |
| --- | --- | --- | --- |
| ChatGPT app / remote MCP | OAuth authorization code + S256 PKCE | `Authorization: Bearer <oauth-access-token>` | `<public-origin>/mcp` |
| Interactive CLI | OAuth authorization code + S256 PKCE, or device authorization | `Authorization: Bearer <oauth-access-token>` | `<public-origin>/api/v2` |
| Machine CLI / direct API | API key file | `X-API-Key: <api-key>` | API key policy; never MCP |

OAuth access tokens and refresh tokens are opaque, hashed at rest, bound to the
issuing public client and exact resource, and revocable. Authorization codes are
single-use. Refresh tokens rotate on every exchange; replay revokes the entire
refresh family. API keys are rejected from the Bearer header, and `/mcp` never
accepts `X-API-Key`.

## Discovery and endpoints

Given `TRACK_ANYWHERE_PUBLIC_BASE_URL=https://ledger.example.com`:

| Purpose | Endpoint |
| --- | --- |
| Authorization-server metadata | `https://ledger.example.com/.well-known/oauth-authorization-server` |
| REST protected-resource metadata | `https://ledger.example.com/.well-known/oauth-protected-resource/api/v2` |
| MCP protected-resource metadata | `https://ledger.example.com/.well-known/oauth-protected-resource/mcp` |
| Dynamic client registration | `https://ledger.example.com/api/v2/oauth/register` |
| Authorization | `https://ledger.example.com/api/v2/oauth/authorize` |
| Device authorization | `https://ledger.example.com/api/v2/oauth/device/authorize` |
| Token and refresh | `https://ledger.example.com/api/v2/oauth/token` |
| Revocation | `https://ledger.example.com/api/v2/oauth/revoke` |
| ChatGPT connector | `https://ledger.example.com/mcp` |

The authorization server accepts JSON for the first-party UI and standard
`application/x-www-form-urlencoded` OAuth requests. Public clients use dynamic
registration or a pre-registered `client_id`; no client secret is issued.

## CLI

Browser PKCE is the interactive default:

```bash
ta --base-url https://ledger.example.com auth login
ta --base-url https://ledger.example.com auth status --json
ta --base-url https://ledger.example.com auth logout
```

Use device authorization only for a genuinely headless environment:

```bash
ta --base-url https://ledger.example.com auth login --device --agent
```

OAuth profiles are keyed by base URL and resource. The CLI prefers the OS
keyring and otherwise writes a permission-restricted profile file. A refresh is
single-flight within a process, so concurrent commands do not replay the same
refresh token. Redirects are disabled for authenticated API calls to prevent
credentials crossing origins.

For a machine API key, use a file owned by the invoking account with no group or
other permissions:

```bash
chmod 600 /run/secrets/track-anywhere-api-key
ta --base-url https://ledger.example.com \
  --api-key-file /run/secrets/track-anywhere-api-key \
  book list --json
```

The environment fallback is intentionally noisy and opt-in:

```bash
TRACK_ANYWHERE_API_KEY='...' \
  ta --insecure-automation --base-url https://ledger.example.com \
  book list --json
```

Do not pass an API key through `--token`, `Authorization`, a URL, or an OAuth
registration field.

## ChatGPT app

Configure the connector URL as `<public-origin>/mcp` and select OAuth. ChatGPT
discovers the authorization server from the Bearer challenge and protected
resource metadata, dynamically registers a public client, and requests
`ledger:read`. The consent screen selects read scopes by default and leaves
`ledger:write` off until the owner explicitly selects it. The eight query tools
remain read-only. Four semantic write tools record expenses, transfers,
credit-card charges, and card payments only after explicit user confirmation;
each requires `ledger:read ledger:write` and a stable `request_id` for exact
retries. Every tool mirrors its OAuth security scheme in both the top-level
descriptor and `_meta`.

The browser supports a private-instance owner account. `POST /api/v2/auth/signup`
requires the existing human owner's personal API key as a high-entropy setup
key and is available only until the first password account is created. Setup
binds the password to the credential's exact user, so its CLI/API-key identity
and browser identity do not diverge. Origin/Referer checks are defense in depth,
not proof of ownership. Later attempts return `409`, and normal sign-in uses
`POST /api/v2/auth/session/password`. Passwords use the schema-pinned
PBKDF2-SHA256 format and are never returned.

An existing personal API key can still be exchanged for an HttpOnly,
CSRF-protected browser session as a recovery-compatible login option. Neither
passwords nor that bootstrap credential are returned to or presented by
ChatGPT. Complete first-owner setup from a trusted browser immediately after a
new private deployment; the UI does not provide password reset or additional
public account registration. Treat the setup key as a secret and never put it
in a URL, log, or chat message.

The single-process application applies bounded token-bucket throttles before
password hashing: one budget per trusted client address and one per normalized
email subject, with `429` and `Retry-After` on exhaustion. There is no shared
process-wide denial bucket, so one client rotating email addresses cannot spend
the login budget of unrelated clients. This protects the supported one-replica
personal deployment from online guessing and PBKDF2 CPU saturation. A
multi-replica topology requires a shared limiter. Configure Uvicorn's
`FORWARDED_ALLOW_IPS` to the exact Dokploy/Traefik network CIDR so the client
budget uses the verified forwarded address, and enforce an independent
client-IP rate limit at the public reverse proxy.

## Local and production composition

For local OAuth, start the single application service. FastAPI hosts the static
consent/device pages, API, discovery metadata, and MCP on the same origin:

```bash
scripts/deploy-local.sh
ta --base-url http://127.0.0.1:8000 auth login
```

Production requires an HTTPS public base:

```dotenv
TRACK_ANYWHERE_MODE=production
TRACK_ANYWHERE_PUBLIC_BASE_URL=https://ledger.example.com
TRACK_ANYWHERE_ALLOWED_ORIGINS=https://ledger.example.com
```

The MCP transport derives its allowed Host from
`TRACK_ANYWHERE_PUBLIC_BASE_URL`, so a direct Traefik-to-FastAPI route needs no
internal Host exception. `TRACK_ANYWHERE_MCP_TRUSTED_PROXY_HOSTS` is reserved
for a topology that actually rewrites the Host header; each value is an exact
internal Host allowlist entry, not a public CORS origin.
