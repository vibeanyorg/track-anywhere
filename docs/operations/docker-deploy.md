# Docker Deployment

Track Anywhere publishes separate runtime images for the API/CLI and the
production web frontend:

```text
ghcr.io/vibeanyorg/track-anywhere-api:stable
ghcr.io/vibeanyorg/track-anywhere-web:stable
ghcr.io/vibeanyorg/track-anywhere-api:nightly
ghcr.io/vibeanyorg/track-anywhere-web:nightly
```

The API image starts the FastAPI service by default and also contains the `ta`
CLI. The web image only contains the Next.js standalone server. Run `ta` inside
the API image when you need a containerized CLI:

```bash
docker run --rm ghcr.io/vibeanyorg/track-anywhere-api:stable ta --help
```

## Service Address

Use `TRACK_ANYWHERE_SERVICE_URL` as the canonical service address for agents and
deployment scripts. The CLI also accepts the older `TRACK_ANYWHERE_API` variable.
Resolution order:

1. `--base-url`
2. `TRACK_ANYWHERE_API`
3. `TRACK_ANYWHERE_SERVICE_URL`
4. `http://localhost:8000`

For local Docker development, read the address from `deploy/env/dev.env`.
For production Docker, read it from `deploy/env/prod.env` on the host, or from
the service manager that starts the container.

## Local Development

One command starts an isolated dev API and Postgres pair:

```bash
scripts/deploy-local.sh
```

The script creates `deploy/env/dev.env` from the example file if needed and
starts the `track-anywhere-dev` Compose project. It reuses the existing local
image for fast restarts; set `TRACK_ANYWHERE_DEV_REBUILD=1` when backend package
changes need a fresh image.

Run the web frontend directly for the fastest edit loop:

```bash
cd frontend
TRACK_ANYWHERE_BACKEND_URL=http://127.0.0.1:8000 npm run dev
```

Useful commands:

```bash
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml ps
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml logs -f api
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml run --rm cli --help
```

## Stable Local Backend

The daily-use Mac mini backend is intentionally separate from the dev stack. It
lives in `/Users/xuyanyue/Documents/track-anywhere-stable-backend`, binds only
to `127.0.0.1:12306`, and points at the Neon `track_anywhere` database.

Build a local stable API image from this source checkout and start the stable
Compose context:

```bash
scripts/build-stable-local-image.sh
scripts/start-stable-local.sh
```

The stable context uses a mode-`0600` API key file rather than the shared macOS
keyring so stale interactive OAuth profiles cannot shadow the local machine
credential:

```bash
export TRACK_ANYWHERE_API=http://127.0.0.1:12306
export TRACK_ANYWHERE_SERVICE_URL=http://127.0.0.1:12306
export TRACK_ANYWHERE_API_KEY_FILE=/Users/xuyanyue/Documents/track-anywhere-stable-backend/secrets/ta-token
```

Verify the running service and common CLI surfaces:

```bash
scripts/stable-smoke.sh
```

Run the release-gate Docker Postgres E2E locally:

```bash
scripts/e2e-docker-postgres.sh
```

This starts an isolated Compose project, issues a long-lived machine token in
local mode, and verifies the common read/write CLI paths against Postgres:
service status, account create, category path ensure/find, expense record,
transaction snapshot, reclassify with `--backup-before`, and targeted Postgres
backup.

## Production/VPS

Prepare `deploy/env/prod.env` on the host. It must include
`TRACK_ANYWHERE_DATABASE_URL`; for public deployments also set
`TRACK_ANYWHERE_ALLOWED_ORIGINS`, `TRACK_ANYWHERE_PUBLIC_BASE_URL`, and
`TRACK_ANYWHERE_AUTH_SESSION_SECRET`. Non-local deployments must also keep the
production security preconditions enabled: `TRACK_ANYWHERE_TLS`,
`TRACK_ANYWHERE_KEY_PROVIDER` or `TRACK_ANYWHERE_ENCRYPTED_VOLUME`,
`TRACK_ANYWHERE_BACKUP_DOC`, `TRACK_ANYWHERE_CLAMAV_HOST`, and
`TRACK_ANYWHERE_CLAMAV_PORT`. The production Compose stack starts ClamAV and the
API streams every attachment through it before storing the original bytes.

`TRACK_ANYWHERE_PUBLIC_BASE_URL` is the browser-facing Web origin, not the
internal `api:8000` address. The Compose stack explicitly trusts only its
`api:8000` reverse-proxy Host for MCP DNS-rebinding validation. If the internal
service name changes, update `TRACK_ANYWHERE_MCP_TRUSTED_PROXY_HOSTS` to the
exact replacement Host value.

Do not mix a canonical HTTPS public base with browser writes sent to an SSH
tunnel origin. Cookie-backed mutations are intentionally bound to the single
`TRACK_ANYWHERE_PUBLIC_BASE_URL`; adding the tunnel to CORS alone does not make
it a trusted CSRF origin. Prefer reaching the tunnel through the canonical
hostname with TLS. For a temporary, tunnel-only local login, set
`TRACK_ANYWHERE_PUBLIC_BASE_URL=http://127.0.0.1:3000`, include that origin in
`TRACK_ANYWHERE_ALLOWED_ORIGINS`, and set
`TRACK_ANYWHERE_AUTH_COOKIE_SECURE=0`. This temporarily changes the OAuth issuer
and resource identifiers, so it cannot run alongside the canonical HTTPS OAuth
configuration. Keep the default secure-cookie behavior for real HTTPS
deployments.

Deploy to the default VPS alias:

```bash
scripts/deploy-vps.sh
```

Deploy to another host:

```bash
scripts/deploy-vps.sh root@example.com
```

The script copies `compose.prod.yaml`, ensures a production env file exists,
fills missing non-secret production defaults, pulls the configured image,
disables legacy `track-anywhere-api.service` and `track-anywhere-web.service`
units if they exist, and starts the `track-anywhere-prod` Compose project. If
the image is in a private registry, log in with `docker login` before running
the script.

## Publishing

Build and publish multi-arch registry images:

```bash
TRACK_ANYWHERE_API_IMAGE=ghcr.io/vibeanyorg/track-anywhere-api \
TRACK_ANYWHERE_WEB_IMAGE=ghcr.io/vibeanyorg/track-anywhere-web \
TRACK_ANYWHERE_IMAGE_TAG=$(git rev-parse --short HEAD) \
scripts/build-public-image.sh
```

The GitHub Actions workflow publishes `nightly` images from `main`, scheduled
runs, and manual nightly dispatch. A `v*` release tag runs the Docker Postgres
E2E gate first, then publishes tag-specific and `stable` multi-arch images.
