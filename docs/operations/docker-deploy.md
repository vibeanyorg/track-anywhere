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

The stable context uses `TRACK_ANYWHERE_TOKEN_FILE` rather than the shared
macOS keyring so stale dev/browser credentials cannot shadow the local machine
token:

```bash
export TRACK_ANYWHERE_API=http://127.0.0.1:12306
export TRACK_ANYWHERE_SERVICE_URL=http://127.0.0.1:12306
export TRACK_ANYWHERE_TOKEN_FILE=/Users/xuyanyue/Documents/track-anywhere-stable-backend/secrets/ta-token
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

For a private SSH-tunnel login at `http://127.0.0.1:3000`, include
`http://127.0.0.1:3000` in `TRACK_ANYWHERE_ALLOWED_ORIGINS` and set
`TRACK_ANYWHERE_AUTH_COOKIE_SECURE=0`. Keep the default secure-cookie behavior
for real HTTPS deployments.

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
