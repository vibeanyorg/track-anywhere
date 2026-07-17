from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_runtime_image_contains_the_static_export_without_a_node_server() -> None:
    dockerfile = _read("Dockerfile")
    dockerignore = _read(".dockerignore")

    assert "FROM node:22-alpine AS web-builder" in dockerfile
    assert "COPY --from=web-builder /app/frontend/out /app/frontend" in dockerfile
    assert "TRACK_ANYWHERE_STATIC_DIRECTORY=/app/frontend" in dockerfile
    assert "FROM node:22-alpine AS web-runtime" not in dockerfile
    assert "EXPOSE 3000" not in dockerfile
    assert "/api/v2/ready" in dockerfile
    assert '"--timeout-graceful-shutdown", "60"' in dockerfile
    assert "**/.env" in dockerignore
    assert "**/.env.*" in dockerignore


def test_image_and_package_contain_the_private_offline_runner() -> None:
    dockerfile = _read("Dockerfile")
    project = tomllib.loads(_read("pyproject.toml"))
    package_discovery = project["tool"]["setuptools"]["packages"]["find"]

    assert "backend/app" in package_discovery["where"]
    assert any(
        pattern in {"track_anywhere*", "track_anywhere.*"}
        for pattern in package_discovery["include"]
    )
    assert (ROOT / "backend/app/track_anywhere/offline/__init__.py").is_file()
    assert (
        ROOT / "backend/app/track_anywhere/offline/import_frozen_financial_history.py"
    ).is_file()
    assert "COPY backend ./backend" in dockerfile
    assert "COPY --from=python-builder /app/backend/app /app/backend/app" in dockerfile


def test_production_compose_has_one_application_and_no_zombie_services() -> None:
    compose = _read("compose.prod.yaml")

    assert "${TRACK_ANYWHERE_IMAGE:?" in compose
    assert ":latest" not in compose
    assert "\n  api:" in compose
    assert "\n  cli:" in compose
    assert "\n  migrate:" in compose
    assert "\n  web:" not in compose
    assert "\n  clamav:" not in compose
    assert "clamav-data" not in compose
    assert "TRACK_ANYWHERE_WEB_IMAGE" not in compose
    assert "TRACK_ANYWHERE_CLAMAV" not in compose
    assert ":3000" not in compose


def test_local_compose_runs_postgres_migration_and_the_single_application() -> None:
    compose = _read("compose.dev.yaml")

    assert "postgres:17-alpine" in compose
    assert "\n  postgres:" in compose
    assert "\n  migrate:" in compose
    assert "service_completed_successfully" in compose
    assert "TRACK_ANYWHERE_DB_RUNTIME_ROLE" in compose
    assert "\n  api:" in compose
    assert "\n  web:" not in compose
    assert "web-runtime" not in compose
    assert ":3000" not in compose
    assert "http://127.0.0.1:8000" in compose


def test_postgres_bootstrap_transfers_database_ownership() -> None:
    bootstrap = _read("docker/postgres/init/001-v2-roles.sh")

    assert "ALTER DATABASE %I OWNER TO %I" in bootstrap
    assert ":'database_name', :'owner_role'" in bootstrap


def test_production_runtime_and_migration_secrets_are_separate() -> None:
    runtime = _read("deploy/env/prod.env.example")
    migration = _read("deploy/env/prod.migrate.env.example")

    assert "TRACK_ANYWHERE_DATABASE_URL=" in runtime
    assert "track_anywhere_runtime" in runtime
    assert "TRACK_ANYWHERE_PROJECTION_POLL_SECONDS=2" in runtime
    assert "MIGRATOR" not in runtime
    assert "TRACK_ANYWHERE_DATABASE_URL=" in migration
    assert "track_anywhere_migrator" in migration
    assert "TRACK_ANYWHERE_DB_RUNTIME_ROLE=track_anywhere_runtime" in migration
    assert "SESSION_SECRET" not in migration
    assert "CLAMAV" not in runtime
    assert "BACKUP_DOC" not in runtime


def test_deployment_helpers_use_the_single_application_endpoint() -> None:
    local = _read("scripts/deploy-local.sh")
    vps = _read("scripts/deploy-vps.sh")

    assert "WEB_" not in local
    assert "TRACK_ANYWHERE_BACKEND_URL" not in local
    assert "Track Anywhere dev: " in local
    assert "TRACK_ANYWHERE_IMAGE" in vps
    assert "TRACK_ANYWHERE_WEB_IMAGE" not in vps
    assert "WEB_" not in vps
    assert "CLAMAV" not in vps
    assert "BACKUP_DOC" not in vps


def test_current_docs_and_defaults_do_not_advertise_the_removed_web_stack() -> None:
    api_app = _read("backend/app/track_anywhere/api/app.py")
    docker_docs = _read("docs/operations/docker-deploy.md")
    oauth_docs = _read("docs/operations/oauth-mcp-auth.md")
    frontend_docs = _read("frontend/README.md")
    publisher = _read("scripts/build-public-image.sh")

    for source in (api_app, docker_docs, oauth_docs, frontend_docs):
        assert ":3000" not in source
    assert "track-anywhere-web" not in docker_docs
    assert "ClamAV" not in docker_docs
    assert "Neon" not in docker_docs
    assert "proxy" not in frontend_docs.casefold()
    assert not (ROOT / "frontend/.env.example").exists()
    assert ":latest" not in publisher


def test_s3_backup_preserves_database_ownership_and_runtime_acl() -> None:
    backup = _read("scripts/backup-postgres-s3.sh")
    restore = _read("scripts/restore-postgres-s3.sh")
    backup_env = _read("deploy/env/backup.env.example")

    assert "pg_dump -Fc" in backup
    assert "--no-owner" not in backup
    assert "--no-acl" not in backup
    assert "rclone rcat" in backup
    assert "TRACK_ANYWHERE_BACKUP_KEEP_LATEST" in backup
    assert 'if [[ "$REMOTE_ROOT" == *: ]]' in backup
    assert "head -n 1" not in backup
    assert "mapfile" not in backup
    assert ".partial." in backup
    assert "gunzip -t" in backup
    assert "pg_restore --list" in backup
    assert "--lock-wait-timeout" in backup
    assert "RCLONE_CONFIG=/etc/track-anywhere/rclone.conf" in backup_env
    assert "RCLONE_TIMEOUT=10m" in backup_env
    assert "pg_restore" in restore
    assert "--clean --if-exists --exit-on-error" in restore
    assert " -O " not in restore
    assert "--no-owner" not in restore
    assert "TRACK_ANYWHERE_RESTORE_CONFIRM" in restore
    assert "TRACK_ANYWHERE_RESTORE_ISOLATED_TARGET" in restore
    assert "TRACK_ANYWHERE_RESTORE_APP_SERVICE" in restore
    assert "target database is not empty" in restore
    assert "rclone cat" in restore
