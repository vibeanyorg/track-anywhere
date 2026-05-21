# Docker Deployment

Track Anywhere publishes one image for both the API service and the `ta` CLI:

```text
ghcr.io/vibeanyorg/track-anywhere:latest
```

The image starts the FastAPI service by default. Run `ta` inside the same image
when you need a containerized CLI:

```bash
docker run --rm ghcr.io/vibeanyorg/track-anywhere:latest ta --help
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

The script creates `deploy/env/dev.env` from the example file if needed, builds
the local image, and starts the `track-anywhere-dev` Compose project.

Useful commands:

```bash
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml ps
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml logs -f api
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml run --rm cli --help
```

## Production/VPS

Prepare `deploy/env/prod.env` on the host. It must include
`TRACK_ANYWHERE_DATABASE_URL`; for public deployments also set
`TRACK_ANYWHERE_ALLOWED_ORIGINS`, `TRACK_ANYWHERE_PUBLIC_BASE_URL`, and
`TRACK_ANYWHERE_AUTH_SESSION_SECRET`.

Deploy to the default VPS alias:

```bash
scripts/deploy-vps.sh
```

Deploy to another host:

```bash
scripts/deploy-vps.sh root@example.com
```

The script copies `compose.prod.yaml`, ensures a production env file exists,
pulls the public image, disables the legacy `track-anywhere-api.service` if it
exists, and starts the `track-anywhere-prod` Compose project.

## Publishing

Build and publish a public multi-arch image:

```bash
TRACK_ANYWHERE_IMAGE=ghcr.io/vibeanyorg/track-anywhere \
TRACK_ANYWHERE_IMAGE_TAG=$(git rev-parse --short HEAD) \
scripts/build-public-image.sh
```

The GitHub Actions workflow also publishes images on `main`, tags, and manual
dispatch.
