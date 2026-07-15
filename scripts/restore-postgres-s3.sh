#!/usr/bin/env bash
set -Eeuo pipefail

: "${TRACK_ANYWHERE_RESTORE_CONTAINER:?set the target PostgreSQL container name or ID}"
: "${TRACK_ANYWHERE_RESTORE_USER:?set the target PostgreSQL admin user}"
: "${TRACK_ANYWHERE_RESTORE_S3_OBJECT:?set the exact rclone object to restore}"

DATABASE="${TRACK_ANYWHERE_RESTORE_DATABASE:-track_anywhere}"
OWNER_ROLE="${TRACK_ANYWHERE_RESTORE_OWNER_ROLE:-track_anywhere_owner}"
MIGRATOR_ROLE="${TRACK_ANYWHERE_RESTORE_MIGRATOR_ROLE:-track_anywhere_migrator}"
RUNTIME_ROLE="${TRACK_ANYWHERE_RESTORE_RUNTIME_ROLE:-track_anywhere_runtime}"

readonly role_pattern='^[a-z_][a-z0-9_]*$'
for role in "$OWNER_ROLE" "$MIGRATOR_ROLE" "$RUNTIME_ROLE"; do
  if [[ ! "$role" =~ $role_pattern ]] || ((${#role} > 63)); then
    printf 'Restore role names must be lowercase PostgreSQL identifiers\n' >&2
    exit 2
  fi
done

if [[ "${TRACK_ANYWHERE_RESTORE_CONFIRM:-}" != "$DATABASE" ]]; then
  printf 'Set TRACK_ANYWHERE_RESTORE_CONFIRM=%s to authorize this destructive restore\n' \
    "$DATABASE" >&2
  exit 2
fi

command -v docker >/dev/null
command -v rclone >/dev/null
command -v gzip >/dev/null
docker inspect "$TRACK_ANYWHERE_RESTORE_CONTAINER" >/dev/null

if [[ -n "${TRACK_ANYWHERE_RESTORE_APP_SERVICE:-}" ]]; then
  docker service inspect "$TRACK_ANYWHERE_RESTORE_APP_SERVICE" >/dev/null
  RUNNING_TASKS="$(
    docker service ps "$TRACK_ANYWHERE_RESTORE_APP_SERVICE" \
      --filter desired-state=running --format '{{.ID}}'
  )"
  if [[ -n "$RUNNING_TASKS" ]]; then
    printf 'Scale the application service to zero before restoring PostgreSQL\n' >&2
    exit 2
  fi
elif [[ "${TRACK_ANYWHERE_RESTORE_ISOLATED_TARGET:-}" != "1" ]]; then
  printf '%s\n' \
    'Set TRACK_ANYWHERE_RESTORE_APP_SERVICE or explicitly mark a fresh isolated target with TRACK_ANYWHERE_RESTORE_ISOLATED_TARGET=1' >&2
  exit 2
fi

case "$(docker exec "$TRACK_ANYWHERE_RESTORE_CONTAINER" pg_restore --version)" in
  "pg_restore (PostgreSQL) 17."*) ;;
  *)
    printf 'Restore requires a PostgreSQL 17 container\n' >&2
    exit 2
    ;;
esac

# Validate every compressed byte and the custom archive TOC before touching the
# target database.
rclone cat "$TRACK_ANYWHERE_RESTORE_S3_OBJECT" | gunzip -t
rclone cat "$TRACK_ANYWHERE_RESTORE_S3_OBJECT" \
  | gunzip -c \
  | docker exec -i "$TRACK_ANYWHERE_RESTORE_CONTAINER" sh -ceu '
      pg_restore --list >/dev/null
      # pg_restore stops after reading the TOC. Keep the parent shell attached
      # to stdin and drain the remaining archive so upstream never sees
      # SIGPIPE on production-sized dumps.
      cat >/dev/null
    '

# An in-place --clean restore is not exact: objects created after the backup
# would survive. Restore only into a newly bootstrapped, otherwise empty DB.
USER_OBJECT_COUNT="$(
  docker exec "$TRACK_ANYWHERE_RESTORE_CONTAINER" \
    psql -U "$TRACK_ANYWHERE_RESTORE_USER" -d "$DATABASE" \
    --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command "
      select count(*)
        from (
          select namespace.oid
            from pg_catalog.pg_namespace namespace
           where namespace.nspname not in ('pg_catalog', 'information_schema', 'public')
             and namespace.nspname not like 'pg_toast%'
             and namespace.nspname not like 'pg_temp_%'
          union all
          select relation.oid
            from pg_catalog.pg_class relation
            join pg_catalog.pg_namespace namespace
              on namespace.oid = relation.relnamespace
           where namespace.nspname not in ('pg_catalog', 'information_schema')
             and namespace.nspname not like 'pg_toast%'
          union all
          select routine.oid
            from pg_catalog.pg_proc routine
            join pg_catalog.pg_namespace namespace
              on namespace.oid = routine.pronamespace
           where namespace.nspname not in ('pg_catalog', 'information_schema')
          union all
          select defaults.oid from pg_catalog.pg_default_acl defaults
        ) user_objects
    " | tr -d '[:space:]'
)"
if [[ "$USER_OBJECT_COUNT" != "0" ]]; then
  printf 'target database is not empty; create a fresh PostgreSQL 17 database for restore\n' >&2
  exit 2
fi

# Bootstrap owner/migrator/runtime roles on the target first. The archive then
# restores the original object owners and exact grants in one transaction.
rclone cat "$TRACK_ANYWHERE_RESTORE_S3_OBJECT" \
  | gunzip -c \
  | docker exec -i "$TRACK_ANYWHERE_RESTORE_CONTAINER" \
      pg_restore -U "$TRACK_ANYWHERE_RESTORE_USER" -d "$DATABASE" \
      --clean --if-exists --exit-on-error --single-transaction

BOUNDARY_OK="$(
  docker exec -i "$TRACK_ANYWHERE_RESTORE_CONTAINER" \
    psql -U "$TRACK_ANYWHERE_RESTORE_USER" -d "$DATABASE" \
    --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --set=owner_role="$OWNER_ROLE" \
    --set=migrator_role="$MIGRATOR_ROLE" \
    --set=runtime_role="$RUNTIME_ROLE" \
    --file=- <<'SQL' | tr -d '[:space:]'
      with selected_roles as (
        select role.oid, role.rolname, role.rolcanlogin, role.rolsuper,
               role.rolcreatedb, role.rolcreaterole, role.rolreplication,
               role.rolbypassrls, role.rolinherit
          from pg_catalog.pg_roles role
         where role.rolname in (:'owner_role', :'migrator_role', :'runtime_role')
      )
      select (
        (select owner.rolname = :'owner_role'
           from pg_catalog.pg_database database
           join pg_catalog.pg_roles owner on owner.oid = database.datdba
          where database.datname = current_database())
        and (select count(*) = 3 from selected_roles)
        and not exists (
          select 1 from selected_roles role
           where role.rolsuper or role.rolcreatedb or role.rolcreaterole
              or role.rolreplication or role.rolbypassrls or role.rolinherit
              or (role.rolname = :'owner_role' and role.rolcanlogin)
              or (role.rolname <> :'owner_role' and not role.rolcanlogin)
        )
        and (select count(*) = 1
               from pg_catalog.pg_auth_members membership
               join pg_catalog.pg_roles granted on granted.oid = membership.roleid
               join pg_catalog.pg_roles member on member.oid = membership.member
              where member.rolname = :'migrator_role'
                and granted.rolname = :'owner_role'
                and not membership.admin_option
                and not membership.inherit_option
                and membership.set_option)
        and not exists (
          select 1
            from pg_catalog.pg_auth_members membership
            join pg_catalog.pg_roles member on member.oid = membership.member
           where member.rolname in (:'owner_role', :'runtime_role')
        )
        and not exists (
          select 1
            from pg_catalog.pg_class relation
            join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
            join pg_catalog.pg_roles owner on owner.oid = relation.relowner
           where namespace.nspname = 'public' and owner.rolname <> :'owner_role'
        )
        and not exists (
          select 1
            from pg_catalog.pg_proc routine
            join pg_catalog.pg_namespace namespace on namespace.oid = routine.pronamespace
            join pg_catalog.pg_roles owner on owner.oid = routine.proowner
           where namespace.nspname = 'public' and owner.rolname <> :'owner_role'
        )
        and not exists (
          select 1
            from pg_catalog.pg_default_acl defaults
            join pg_catalog.pg_roles owner on owner.oid = defaults.defaclrole
           where owner.rolname <> :'owner_role'
        )
        and has_schema_privilege(:'runtime_role', 'public', 'USAGE')
        and not has_schema_privilege(:'runtime_role', 'public', 'CREATE')
      );
SQL
)"
if [[ "$BOUNDARY_OK" != "t" ]]; then
  printf 'restored database owner, roles, memberships, or object ACL boundary is invalid\n' >&2
  exit 1
fi

docker exec "$TRACK_ANYWHERE_RESTORE_CONTAINER" \
  psql -U "$TRACK_ANYWHERE_RESTORE_USER" -d "$DATABASE" \
  --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --command 'select version_num from public.alembic_version'
