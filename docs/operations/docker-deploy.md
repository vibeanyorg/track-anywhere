# Docker Deployment

Track Anywhere publishes one multi-architecture application image:

```text
ghcr.io/vibeanyorg/track-anywhere-api:<immutable-tag-or-digest>
```

The image contains FastAPI, the statically exported browser UI, REST, OAuth,
MCP, the supervised monthly projection loop, Alembic, and the `ta` CLI. Node.js
is used only by the image build. The production process listens on port 8000;
no second HTTP or worker service is needed.

## Local Docker Compose

Start PostgreSQL 17, run the idempotent role bootstrap and Alembic migration,
then start the application:

```bash
scripts/deploy-local.sh
```

The resulting URL is stored in `deploy/env/dev.env` and printed by the script.
Useful commands:

```bash
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml ps
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml logs -f api
docker compose --env-file deploy/env/dev.env -f compose.dev.yaml run --rm cli --help
```

Set `TRACK_ANYWHERE_DEV_REBUILD=1` after Dockerfile, Python dependency, or
frontend changes. The Compose database uses a named local volume; remove that
volume only when a deliberate destructive reset is wanted.

## Standalone production Compose

Prepare two mode-`0600` files on the host:

- `deploy/env/prod.env`: immutable image reference, runtime-role DSN, and public
  origin settings used by the long-running application.
- `deploy/env/prod.migrate.env`: migrator-role DSN and runtime role name used
  only by the one-shot migration container.

Then run the migration before replacing the application:

```bash
docker compose --env-file deploy/env/prod.env -f compose.prod.yaml pull api migrate
docker compose --env-file deploy/env/prod.env -f compose.prod.yaml \
  --profile migrate run --rm migrate
docker compose --env-file deploy/env/prod.env -f compose.prod.yaml \
  up -d --remove-orphans api
curl --fail http://127.0.0.1:8000/api/v2/ready
curl --fail http://127.0.0.1:8000/
```

The application must receive only the runtime DSN. Do not put the migrator or
database-admin DSN in its environment. Keep the host binding on loopback and
terminate public HTTPS at Traefik or Caddy.

The legacy VPS helper performs this same migration-before-start sequence and
requires an immutable image reference:

```bash
TRACK_ANYWHERE_IMAGE='ghcr.io/vibeanyorg/track-anywhere-api@sha256:...' \
scripts/deploy-vps.sh root@example.com
```

For Dokploy, use the dedicated
[Dokploy deployment and backup runbook](dokploy-deploy.md).

## Publishing

Build and push one immutable multi-architecture tag:

```bash
TRACK_ANYWHERE_IMAGE=ghcr.io/vibeanyorg/track-anywhere-api \
TRACK_ANYWHERE_IMAGE_TAG=$(git rev-parse --short HEAD) \
scripts/build-public-image.sh
```

GitHub Actions publishes convenient channel tags plus commit-derived tags.
Deploy the commit tag or registry digest, not a mutable channel tag. Every
release runs the PostgreSQL 17, migration, E2E, backend, CLI, and frontend gates
before image publication.
