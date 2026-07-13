#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${TRACK_ANYWHERE_OWNER_ROLE:?TRACK_ANYWHERE_OWNER_ROLE is required}"
: "${TRACK_ANYWHERE_MIGRATOR_ROLE:?TRACK_ANYWHERE_MIGRATOR_ROLE is required}"
: "${TRACK_ANYWHERE_MIGRATOR_PASSWORD:?TRACK_ANYWHERE_MIGRATOR_PASSWORD is required}"
: "${TRACK_ANYWHERE_RUNTIME_ROLE:?TRACK_ANYWHERE_RUNTIME_ROLE is required}"
: "${TRACK_ANYWHERE_RUNTIME_PASSWORD:?TRACK_ANYWHERE_RUNTIME_PASSWORD is required}"

if (( ${#POSTGRES_DB} > 63 )); then
  echo "PostgreSQL database names must not exceed the 63-byte identifier limit" >&2
  exit 2
fi

readonly role_pattern='^[a-z_][a-z0-9_]*$'
for role in \
  "$TRACK_ANYWHERE_OWNER_ROLE" \
  "$TRACK_ANYWHERE_MIGRATOR_ROLE" \
  "$TRACK_ANYWHERE_RUNTIME_ROLE"; do
  if [[ ! "$role" =~ $role_pattern ]]; then
    echo "PostgreSQL role names must be lowercase identifiers" >&2
    exit 2
  fi
  if (( ${#role} > 63 )); then
    echo "PostgreSQL role names must not exceed the 63-byte identifier limit" >&2
    exit 2
  fi
done

if [[ "$TRACK_ANYWHERE_OWNER_ROLE" == "$TRACK_ANYWHERE_MIGRATOR_ROLE" ]] ||
  [[ "$TRACK_ANYWHERE_OWNER_ROLE" == "$TRACK_ANYWHERE_RUNTIME_ROLE" ]] ||
  [[ "$TRACK_ANYWHERE_MIGRATOR_ROLE" == "$TRACK_ANYWHERE_RUNTIME_ROLE" ]]; then
  echo "owner, migrator, and runtime roles must be distinct" >&2
  exit 2
fi

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 <<'SQL'
\getenv owner_role TRACK_ANYWHERE_OWNER_ROLE
\getenv migrator_role TRACK_ANYWHERE_MIGRATOR_ROLE
\getenv migrator_password TRACK_ANYWHERE_MIGRATOR_PASSWORD
\getenv runtime_role TRACK_ANYWHERE_RUNTIME_ROLE
\getenv runtime_password TRACK_ANYWHERE_RUNTIME_PASSWORD
\getenv database_name POSTGRES_DB

SELECT format(
  'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
  :'owner_role'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner_role')
\gexec

SELECT format(
  'ALTER ROLE %I WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
  :'owner_role'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
  :'migrator_role',
  :'migrator_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrator_role')
\gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
  :'migrator_role',
  :'migrator_password'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
  :'runtime_role',
  :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_role')
\gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
  :'runtime_role',
  :'runtime_password'
)
\gexec

SELECT format(
  'REVOKE %I FROM %I GRANTED BY %I CASCADE',
  granted.rolname,
  member.rolname,
  grantor.rolname
)
FROM pg_auth_members membership
JOIN pg_roles granted ON granted.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
JOIN pg_roles grantor ON grantor.oid = membership.grantor
WHERE member.rolname = :'owner_role'
\gexec

SELECT format(
  'REVOKE %I FROM %I GRANTED BY %I CASCADE',
  granted.rolname,
  member.rolname,
  grantor.rolname
)
FROM pg_auth_members membership
JOIN pg_roles granted ON granted.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
JOIN pg_roles grantor ON grantor.oid = membership.grantor
WHERE member.rolname = :'runtime_role'
\gexec

SELECT format(
  'REVOKE %I FROM %I GRANTED BY %I CASCADE',
  granted.rolname,
  member.rolname,
  grantor.rolname
)
FROM pg_auth_members membership
JOIN pg_roles granted ON granted.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
JOIN pg_roles grantor ON grantor.oid = membership.grantor
WHERE member.rolname = :'migrator_role'
\gexec

SELECT format(
  'GRANT %I TO %I WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',
  :'owner_role',
  :'migrator_role'
)
\gexec

SELECT format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', :'database_name')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'migrator_role')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'runtime_role')
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
