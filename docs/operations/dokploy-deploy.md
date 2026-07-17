# Dokploy Deployment and S3/R2 Recovery

This runbook was checked against the Dokploy documentation and the official
`canary` source at commit `df3965a5816700d61c39fd1a13241b5766d7b24e` on
2026-07-15. It prepares the deployment but does not authorize a production
cutover.

## Target topology

Only two containers stay running:

```text
Internet -> Dokploy Traefik -> Track Anywhere :8000 -> PostgreSQL 17
```

- One Dokploy Application runs FastAPI and serves the static UI, REST, OAuth,
  discovery, MCP, and the supervised monthly projection loop. PostgreSQL
  advisory locks elect one projection leader across rolling replicas and fence
  each Book batch, so no separate worker container is needed.
- One Dokploy PostgreSQL Database stores the ledger.
- Database bootstrap, Alembic migration, backup, and restore are one-shot jobs,
  not services.
- No application volume, public database port, Node.js runtime, cache, queue, or
  separate worker service is required.
- ClamAV is not used, and there is no separate port 3000 web service. FastAPI
  serves the static export and all public protocols on port 8000.

Dokploy recommends building production images in CI and deploying the
prebuilt image. Pin a commit tag or digest; do not deploy `latest`.

## 1. Create PostgreSQL 17

In the same Dokploy project/environment as the Application, create a PostgreSQL
Database with:

- image/version: `postgres:17-alpine` explicitly;
- database: `track_anywhere`;
- a strong generated Dokploy admin password;
- no External Port.

Use the Database page's Internal Credentials for application and maintenance
jobs. External credentials are unnecessary for normal operation. Dokploy keeps
the data in the Database resource's named volume.

Track Anywhere requires separate owner, migrator, and runtime roles. On the
Dokploy host, create a mode-`0600` `/etc/track-anywhere/db-bootstrap.env` from
the internal credentials:

```dotenv
PGHOST=<dokploy-internal-postgres-host>
PGPORT=5432
PGPASSWORD=<dokploy-admin-password>
POSTGRES_USER=<dokploy-admin-user>
POSTGRES_DB=track_anywhere
TRACK_ANYWHERE_OWNER_ROLE=track_anywhere_owner
TRACK_ANYWHERE_MIGRATOR_ROLE=track_anywhere_migrator
TRACK_ANYWHERE_MIGRATOR_PASSWORD=<generated-migrator-password>
TRACK_ANYWHERE_RUNTIME_ROLE=track_anywhere_runtime
TRACK_ANYWHERE_RUNTIME_PASSWORD=<generated-runtime-password>
```

Stream the versioned bootstrap script from this checkout to a temporary
PostgreSQL 17 client on Dokploy's internal network:

```bash
ssh root@DOKPLOY_HOST \
  'docker run --rm -i --network dokploy-network \
   --env-file /etc/track-anywhere/db-bootstrap.env \
   postgres:17-alpine bash -s' \
  < docker/postgres/init/001-v2-roles.sh
```

The script is idempotent, transfers database ownership to the non-login owner,
and leaves the migrator with SET-only owner membership. Copy the runtime secret
to the Application environment and the migrator secret to the one-shot
migration environment, then remove the admin bootstrap file.

## 2. Run migrations before deployment

Dokploy does not currently document a native release/pre-deploy command. Do not
run Alembic from every application startup. Put only these values in a
mode-`0600` `/etc/track-anywhere/migrate.env` on the host:

```dotenv
TRACK_ANYWHERE_DATABASE_URL=postgresql+psycopg://track_anywhere_migrator:<url-encoded-password>@<internal-host>:5432/track_anywhere
TRACK_ANYWHERE_DB_RUNTIME_ROLE=track_anywhere_runtime
```

For every release, pull the exact application digest and run the migration as
a disposable container on `dokploy-network`:

```bash
docker pull 'ghcr.io/vibeanyorg/track-anywhere-api@sha256:...'
docker run --rm --network dokploy-network \
  --env-file /etc/track-anywhere/migrate.env \
  'ghcr.io/vibeanyorg/track-anywhere-api@sha256:...' \
  python -m alembic upgrade head
```

Abort the release if this command fails. The long-running Application must
never receive the migrator or Dokploy admin DSN.

## 3. Configure the Application

Deploy the same immutable image with container port `8000` and these runtime
variables:

```dotenv
TRACK_ANYWHERE_MODE=production
TRACK_ANYWHERE_DATABASE_URL=postgresql+psycopg://track_anywhere_runtime:<url-encoded-password>@<internal-host>:5432/track_anywhere
TRACK_ANYWHERE_PUBLIC_BASE_URL=https://ledger.example.com
TRACK_ANYWHERE_ALLOWED_ORIGINS=https://ledger.example.com
TRACK_ANYWHERE_PROJECTION_POLL_SECONDS=2
TRACK_ANYWHERE_PROTECTED_CONTENT_KEYRING_FILE=/run/secrets/track-anywhere-protected-content-keyring.json
```

Create `/etc/track-anywhere/protected-content-keyring.json` outside the
checkout, owned by the numeric runtime UID and readable only by that owner
(`0400` or `0600`). Configure a read-only bind mount from that host path to
`/run/secrets/track-anywhere-protected-content-keyring.json`. Store only this
fixed path in the Application environment. Never store a raw or base64-encoded
master key in Dokploy environment variables, Compose, logs, or the repository.
Back up the keyring and its recovery instructions separately from PostgreSQL;
do not print its content while validating the mount.

In Domains, attach the public hostname to port 8000 and let Dokploy/Traefik
manage HTTPS. Do not add a host `ports:` mapping. In Advanced/Swarm settings,
set Stop Grace Period to at least 65 seconds (`65000000000` nanoseconds) so an
in-flight projection batch can commit or roll back before Docker sends SIGKILL.
The image healthcheck calls
`/api/v2/ready`, which verifies PostgreSQL 17, the runtime identity, schema
generation, and exact Alembic head. After deployment, also verify:

```bash
curl --fail https://ledger.example.com/
curl --fail https://ledger.example.com/api/v2/health
curl --fail https://ledger.example.com/api/v2/ready
curl --fail https://ledger.example.com/.well-known/oauth-authorization-server
```

For a new private instance, open `/auth/signup` from the canonical HTTPS origin
and complete the one-time owner setup using the human owner's personal API key
as the setup key. Never pass that key in a URL or log. Confirm sign-out and
email/password sign-in at `/auth/login`; subsequent signup attempts must return
`409`. Existing personal API-key login remains available as a secondary
recovery path.

Keep the Application at one replica unless the in-process authentication
throttle is replaced by a shared limiter. Add a Traefik client-IP rate-limit
middleware for the password-login and signup paths as an independent edge
layer. Set `FORWARDED_ALLOW_IPS` to the exact CIDR of the private Docker network
used by Dokploy/Traefik; do not use `*` when the application can also be reached
without that proxy. This lets Uvicorn replace the peer address only for trusted
proxy connections. Do not rely on `Origin` as a client identity or ownership
proof.

The supported entry point is `track_anywhere.server:app`; it starts the
projection supervisor in the FastAPI lifespan. A rolling overlap is safe, but
one Application replica is sufficient for a personal deployment. Never replace
the image command with `track_anywhere.api.app:app`, because that API-only
composition root intentionally has no static site, MCP, or projection runtime.

## One-time frozen financial-history job

The optional `frozen-v1-backfill` Compose profile is a private, disposable job
using the exact same immutable API image, runtime DSN, and read-only keyring
mount as the Application. It has no public port and is never a long-running
Dokploy service. Do not add it as another Application or expose it through
Traefik.

Follow [the frozen V1 financial-history runbook](v1-financial-backfill.md) and
its separate production-authorization gate. The runbook requires a validated
backup/fresh PostgreSQL 17 restore, two-target rehearsal, maintenance write
block, stdin-only atomic apply, independent verification, and restore-and-switch
recovery. The profile must not be run merely because it exists in the Compose
file.

## 4. Use an ACL-preserving S3/R2 archive

Dokploy's Database Backup UI is useful for ordinary databases, supports S3/R2,
cron, prefixes, Test, and Keep Latest. It is not sufficient as Track Anywhere's
only recovery source. The current implementation runs `pg_dump` with
`--no-acl --no-owner`, then restores with ownership disabled. That deliberately
omits the exact GRANT/REVOKE, default privileges, and object owners enforced by
this ledger.

Use `scripts/backup-postgres-s3.sh` on the Dokploy host instead. It streams a
custom-format dump to a temporary rclone object while preserving ownership and
ACL metadata. It downloads the complete object, verifies both the gzip checksum
and `pg_restore --list`, promotes only a verified archive to its final key, and
then enforces retention. A partial or invalid stream is deleted and never counts
toward retention.

Configure an rclone S3 remote with a token limited to one backup bucket; for
Cloudflare R2 follow the same bucket-scoped credential guidance as Dokploy's R2
destination documentation. For personal financial data, place an rclone
`crypt` remote over that bucket and use the crypt remote in `backup.env`, so
objects are encrypted before upload. Keep both the rclone config and its
separately recorded crypt recovery secret outside the Dokploy database volume.

The archive contains object owners, GRANT/REVOKE entries, and default ACLs. A
single-database `pg_dump` does not contain cluster role definitions/passwords or
database-level ownership/ACL, so the versioned role bootstrap remains a required
part of every restore.

Install the script and example timer without enabling them yet:

```bash
install -m 0755 scripts/backup-postgres-s3.sh \
  /usr/local/libexec/track-anywhere-backup
install -m 0644 deploy/systemd/track-anywhere-backup.service \
  /etc/systemd/system/track-anywhere-backup.service
install -m 0644 deploy/systemd/track-anywhere-backup.timer \
  /etc/systemd/system/track-anywhere-backup.timer
install -d -m 0700 /etc/track-anywhere
install -m 0600 deploy/env/backup.env.example \
  /etc/track-anywhere/backup.env
rclone --config /etc/track-anywhere/rclone.conf config
chmod 0600 /etc/track-anywhere/rclone.conf
```

Edit `backup.env` with the stable Dokploy Database `appName`/Swarm service name,
admin user, and rclone crypt remote. The script resolves the current task
container on every run, so a database container restart does not break the
timer. The default policy is one archive every six hours,
keeping 120 archives (about 30 days). Keep separate monthly copies or configure
an S3/R2 lifecycle as a second retention tier. Only after a manual successful
run and full archive validation should the timer be enabled:

```bash
systemctl daemon-reload
systemctl start track-anywhere-backup.service
journalctl -u track-anywhere-backup.service --no-pager
systemctl enable --now track-anywhere-backup.timer
systemctl list-timers track-anywhere-backup.timer
```

The unit has a two-hour hard timeout; `pg_dump` also gives up rather than wait
forever on a DDL lock, and rclone has bounded connection/data timeouts and
retries. Connect `systemd` failure state and backup freshness to the host's
existing alerting. On a multi-node Swarm, install this timer on the node that
runs the PostgreSQL task (or replace `docker exec` with a locked-down internal
network client); the local Docker task lookup cannot cross nodes. A normal
single-server Dokploy installation does not have that constraint.

Dokploy Web Server Backups cover Dokploy's own database and `/etc/dokploy`, not
this business database. Configure that separately for control-plane recovery.
Do not use a live PostgreSQL volume copy as the primary database backup.

## 5. Prove restore, not just upload

At least quarterly, create a fresh disposable Dokploy PostgreSQL 17 Database and
run the same role bootstrap against it. Do not restore over an existing
database: `pg_restore --clean` cannot remove objects that are absent from the
archive, so an in-place result is not an exact point-in-time copy. The restore
script verifies that the target has no user objects and requires an explicit
isolated-target acknowledgement:

```bash
TRACK_ANYWHERE_RESTORE_CONTAINER=<disposable-postgres-container> \
TRACK_ANYWHERE_RESTORE_USER=<dokploy-admin-user> \
TRACK_ANYWHERE_RESTORE_DATABASE=track_anywhere \
TRACK_ANYWHERE_RESTORE_S3_OBJECT='track-r2-crypt:track-anywhere/postgres/six-hourly/<archive>.dump.gz' \
TRACK_ANYWHERE_RESTORE_CONFIRM=track_anywhere \
TRACK_ANYWHERE_RESTORE_ISOLATED_TARGET=1 \
scripts/restore-postgres-s3.sh
```

The restore command validates the entire remote archive before changing the
database, preserves archived object owners and ACLs, uses one transaction, and
checks the bootstrapped database owner, role attributes/membership, object
owners, schema boundary, and default-ACL ownership. For a production recovery,
use the same blue/green flow with a fresh replacement Database; leave the old
Application pointed at the old database until the candidate passes.

Next, run the exact candidate image's normal one-shot Alembic command with the
replacement database's migrator DSN. Even when the archive is already at head,
the Alembic environment revalidates the exact runtime privilege/default-ACL
matrix. Start a disposable Application with the replacement runtime DSN,
require `/api/v2/ready` to return 200, exercise login plus one authenticated
read, and then run the independent ledger verifier:

```bash
docker run --rm --network dokploy-network --env-file /etc/track-anywhere/restore-runtime.env \
  'ghcr.io/vibeanyorg/track-anywhere-api@sha256:...' python -c \
  'import json,os; from track_anywhere.verification import verify_v2_ledger; r=verify_v2_ledger(os.environ["TRACK_ANYWHERE_DATABASE_URL"]); print(json.dumps(r.to_dict(),sort_keys=True)); raise SystemExit(r.status != "PASS")'
```

Do not call a backup complete until archive validation, fresh-target restore,
Alembic privilege validation, API/auth smoke, and the ledger verifier all pass.
Only after all checks pass should the production Application DSN be switched to
the replacement database. Keep the previous database untouched until the new
deployment has remained healthy through the rollback window.

## Official references

- [Databases](https://docs.dokploy.com/docs/core/databases)
- [Database connections](https://docs.dokploy.com/docs/core/databases/connection)
- [Database backups](https://docs.dokploy.com/docs/core/databases/backups)
- [Database restore](https://docs.dokploy.com/docs/core/databases/restore)
- [Cloudflare R2 destination](https://docs.dokploy.com/docs/core/cloudflare-r2)
- [Dokploy control-plane backups](https://docs.dokploy.com/docs/core/backups)
- [Volume backups](https://docs.dokploy.com/docs/core/volume-backups)
- [Going to production](https://docs.dokploy.com/docs/core/applications/going-production)
- [Compose domains](https://docs.dokploy.com/docs/core/docker-compose/domains)
- [PostgreSQL backup flags in Dokploy source](https://github.com/Dokploy/dokploy/blob/df3965a5816700d61c39fd1a13241b5766d7b24e/packages/server/src/utils/backups/utils.ts#L93-L98)
- [PostgreSQL restore flags in Dokploy source](https://github.com/Dokploy/dokploy/blob/df3965a5816700d61c39fd1a13241b5766d7b24e/packages/server/src/utils/restore/utils.ts#L6-L12)
