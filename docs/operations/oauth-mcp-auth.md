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
`ledger:read`. Every exposed MCP tool is read-only, idempotent, and mirrors its
OAuth security scheme in both the top-level descriptor and `_meta`.

The existing browser sign-in uses a personal API key once to create an
HttpOnly, CSRF-protected browser session. That bootstrap credential remains on
the Track Anywhere origin; it is never returned to or presented by ChatGPT.

## Local and production composition

For local OAuth, start both the Web and API services. The public base must be
the Web origin because it hosts consent and device pages while proxying the API,
discovery metadata, and MCP transport:

```bash
scripts/deploy-local.sh
ta --base-url http://127.0.0.1:3000 auth login
```

Production requires an HTTPS public base:

```dotenv
TRACK_ANYWHERE_MODE=production
TRACK_ANYWHERE_PUBLIC_BASE_URL=https://ledger.example.com
TRACK_ANYWHERE_ALLOWED_ORIGINS=https://ledger.example.com
```

The standard Compose topology also sets
`TRACK_ANYWHERE_MCP_TRUSTED_PROXY_HOSTS=api:8000`. This is an exact internal Host
allowlist for SDK DNS-rebinding protection, not a public CORS allowlist. Change
it only when the internal reverse-proxy target changes.
