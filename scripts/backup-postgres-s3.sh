#!/usr/bin/env bash
set -Eeuo pipefail

: "${TRACK_ANYWHERE_BACKUP_USER:?set the Dokploy PostgreSQL admin user}"
: "${TRACK_ANYWHERE_BACKUP_S3_REMOTE:?set an rclone bucket or crypt remote}"

DATABASE="${TRACK_ANYWHERE_BACKUP_DATABASE:-track_anywhere}"
PREFIX="${TRACK_ANYWHERE_BACKUP_PREFIX:-track-anywhere/postgres/daily}"
KEEP_LATEST="${TRACK_ANYWHERE_BACKUP_KEEP_LATEST:-30}"
LOCK_WAIT_TIMEOUT="${TRACK_ANYWHERE_BACKUP_LOCK_WAIT_TIMEOUT:-60000}"

if [[ ! "$KEEP_LATEST" =~ ^[1-9][0-9]*$ ]]; then
  printf 'TRACK_ANYWHERE_BACKUP_KEEP_LATEST must be a positive integer\n' >&2
  exit 2
fi
if [[ ! "$LOCK_WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  printf 'TRACK_ANYWHERE_BACKUP_LOCK_WAIT_TIMEOUT must be positive milliseconds\n' >&2
  exit 2
fi

REMOTE_ROOT="${TRACK_ANYWHERE_BACKUP_S3_REMOTE%/}"
if [[ "$REMOTE_ROOT" == *: ]]; then
  REMOTE_BASE="${REMOTE_ROOT}${PREFIX#/}"
else
  REMOTE_BASE="${REMOTE_ROOT}/${PREFIX#/}"
fi
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="track-anywhere-${TIMESTAMP}.dump.gz"
REMOTE_OBJECT="$REMOTE_BASE/$ARCHIVE"
PARTIAL_OBJECT="${REMOTE_OBJECT}.partial.$$"

command -v docker >/dev/null
command -v rclone >/dev/null
command -v gzip >/dev/null
CONTAINER="${TRACK_ANYWHERE_BACKUP_CONTAINER:-}"
if [[ -z "$CONTAINER" && -n "${TRACK_ANYWHERE_BACKUP_SERVICE:-}" ]]; then
  CONTAINER="$(
    docker ps -q --filter status=running \
      --filter "label=com.docker.swarm.service.name=$TRACK_ANYWHERE_BACKUP_SERVICE" \
      | sed -n '1p'
  )"
fi
if [[ -z "$CONTAINER" ]]; then
  printf 'Set TRACK_ANYWHERE_BACKUP_SERVICE or TRACK_ANYWHERE_BACKUP_CONTAINER\n' >&2
  exit 2
fi
docker inspect "$CONTAINER" >/dev/null
case "$(docker exec "$CONTAINER" pg_dump --version)" in
  "pg_dump (PostgreSQL) 17."*) ;;
  *)
    printf 'Backup requires a PostgreSQL 17 container\n' >&2
    exit 2
    ;;
esac

cleanup_partial() {
  rclone deletefile "$PARTIAL_OBJECT" >/dev/null 2>&1 || true
}
trap cleanup_partial EXIT

# Keep PostgreSQL ownership, grants, and default privileges in the archive.
# The three-role runtime boundary depends on all of that metadata at restore.
docker exec "$CONTAINER" sh -ceu '
  export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is not set in the database container}"
  exec pg_dump -Fc --compress=0 -h localhost -U "$1" --no-password \
    --lock-wait-timeout="$3" "$2"
' sh "$TRACK_ANYWHERE_BACKUP_USER" "$DATABASE" "$LOCK_WAIT_TIMEOUT" \
  | gzip -c \
  | rclone rcat "$PARTIAL_OBJECT"

# Read the complete remote object back. The gzip checksum catches truncated
# payloads, while pg_restore validates the custom archive header and TOC.
rclone cat "$PARTIAL_OBJECT" | gunzip -t
rclone cat "$PARTIAL_OBJECT" \
  | gunzip -c \
  | docker exec -i "$CONTAINER" sh -ceu '
      pg_restore --list >/dev/null
      # pg_restore stops after reading the TOC. Keep the parent shell attached
      # to stdin and drain the remaining archive so upstream never sees
      # SIGPIPE on production-sized dumps.
      cat >/dev/null
    '
rclone moveto "$PARTIAL_OBJECT" "$REMOTE_OBJECT"
trap - EXIT

ARCHIVE_LIST="$(
  rclone lsf "$REMOTE_BASE" --files-only --include 'track-anywhere-*.dump.gz'
)"
ARCHIVES=()
if [[ -n "$ARCHIVE_LIST" ]]; then
  while IFS= read -r archive; do
    [[ -n "$archive" ]] && ARCHIVES+=("$archive")
  done < <(LC_ALL=C sort <<<"$ARCHIVE_LIST")
fi
EXCESS=$((${#ARCHIVES[@]} - KEEP_LATEST))
if ((EXCESS > 0)); then
  for ((index = 0; index < EXCESS; index += 1)); do
    rclone deletefile "$REMOTE_BASE/${ARCHIVES[$index]}"
  done
fi

printf '%s\n' "$REMOTE_OBJECT"
