"""Guard a clean PostgreSQL 17 target and establish the V2 schema marker."""

from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa


revision = "v2_0001_schema_guard"
down_revision = None
branch_labels = None
depends_on = None

_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier) or len(identifier.encode("ascii")) > 63:
        raise RuntimeError(
            "database runtime role must be a safe lowercase PostgreSQL identifier"
        )
    return f'"{identifier}"'


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not value:
        raise RuntimeError("TRACK_ANYWHERE_DB_RUNTIME_ROLE is required")
    _quote_identifier(value)
    return value


def _verified_version_table(connection: sa.Connection) -> bool:
    relation = (
        connection.execute(
            sa.text(
                """
            select relation.relkind,
                   relation.relpersistence,
                   relation.relispartition,
                   relation.relrowsecurity,
                   relation.relforcerowsecurity,
                   relation.relhasrules,
                   relation.relhastriggers,
                   relation.reloptions is null as has_default_options,
                   table_access_method.amname::text as table_access_method,
                   owner.rolname::text as owner_role,
                   relation.relacl is null as has_default_acl,
                   database_owner.rolname::text as database_owner,
                   (select count(*)
                      from pg_catalog.pg_policy policy
                     where policy.polrelid = relation.oid) as policy_count,
                   (select count(*)
                      from pg_catalog.pg_trigger trigger
                     where trigger.tgrelid = relation.oid
                       and not trigger.tgisinternal) as user_trigger_count,
                   (select count(*)
                      from pg_catalog.pg_rewrite rule
                     where rule.ev_class = relation.oid) as rule_count,
                   (select count(*)
                      from pg_catalog.pg_inherits inheritance
                     where inheritance.inhrelid = relation.oid
                        or inheritance.inhparent = relation.oid) as inheritance_count
              from pg_catalog.pg_class relation
              join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
              join pg_catalog.pg_am table_access_method on table_access_method.oid = relation.relam
              join pg_catalog.pg_roles owner on owner.oid = relation.relowner
              join pg_catalog.pg_database database on database.datname = current_database()
              join pg_catalog.pg_roles database_owner on database_owner.oid = database.datdba
             where namespace.nspname = 'public'
               and relation.relname = 'alembic_version'
            """
            )
        )
        .mappings()
        .all()
    )
    if len(relation) != 1:
        return False
    row = relation[0]
    if (
        row["relkind"] != "r"
        or row["relpersistence"] != "p"
        or row["relispartition"]
        or row["relrowsecurity"]
        or row["relforcerowsecurity"]
        or row["relhasrules"]
        or row["relhastriggers"]
        or not row["has_default_options"]
        or row["table_access_method"] != "heap"
        or row["owner_role"] != row["database_owner"]
        or not row["has_default_acl"]
        or row["policy_count"] != 0
        or row["user_trigger_count"] != 0
        or row["rule_count"] != 0
        or row["inheritance_count"] != 0
    ):
        return False

    columns = connection.execute(
        sa.text(
            """
            select attribute.attname::text as column_name,
                   pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) as data_type,
                   attribute.attnotnull,
                   not attribute.atthasdef as has_no_default,
                   attribute.attidentity = '' as has_no_identity,
                   attribute.attgenerated = '' as has_no_generated_value,
                   attribute.attacl is null as has_default_acl,
                   attribute_default.oid is null as has_no_attribute_default
              from pg_catalog.pg_attribute attribute
              join pg_catalog.pg_class relation on relation.oid = attribute.attrelid
              join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
              left join pg_catalog.pg_attrdef attribute_default
                on attribute_default.adrelid = attribute.attrelid
               and attribute_default.adnum = attribute.attnum
             where namespace.nspname = 'public'
               and relation.relname = 'alembic_version'
               and attribute.attnum > 0
               and not attribute.attisdropped
             order by attribute.attnum
            """
        )
    ).all()
    if [tuple(column) for column in columns] != [
        ("version_num", "character varying(32)", True, True, True, True, True, True)
    ]:
        return False

    primary_key = connection.execute(
        sa.text(
            """
            select catalog_constraint.conname::text,
                   catalog_constraint.contype,
                   catalog_constraint.conkey::smallint[],
                   not catalog_constraint.condeferrable as is_not_deferrable,
                   not catalog_constraint.condeferred as is_not_initially_deferred,
                   catalog_constraint.convalidated,
                   catalog_constraint.conparentid = 0 as has_no_parent,
                   catalog_constraint.conislocal,
                   catalog_constraint.coninhcount = 0 as is_not_inherited,
                   catalog_constraint.connoinherit,
                   index_relation.relname::text as index_name
              from pg_catalog.pg_constraint catalog_constraint
              join pg_catalog.pg_class relation on relation.oid = catalog_constraint.conrelid
              join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
              left join pg_catalog.pg_class index_relation
                on index_relation.oid = catalog_constraint.conindid
             where namespace.nspname = 'public'
               and relation.relname = 'alembic_version'
             order by catalog_constraint.conname
            """
        )
    ).all()
    if [tuple(constraint) for constraint in primary_key] != [
        (
            "alembic_version_pkc",
            "p",
            [1],
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            "alembic_version_pkc",
        )
    ]:
        return False

    indexes = connection.execute(
        sa.text(
            """
            select index_relation.relname::text as index_name,
                   index_relation.relkind,
                   index_relation.relpersistence,
                   index_relation.relacl is null as has_default_acl,
                   index_relation.reloptions is null as has_default_options,
                   index_owner.rolname::text as index_owner,
                   database_owner.rolname::text as database_owner,
                   access_method.amname::text as access_method,
                   index_catalog.indnatts,
                   index_catalog.indnkeyatts,
                   index_catalog.indkey::smallint[] as indexed_columns,
                   index_catalog.indisunique,
                   not index_catalog.indnullsnotdistinct as nulls_are_distinct,
                   index_catalog.indisprimary,
                   not index_catalog.indisexclusion as is_not_exclusion,
                   index_catalog.indimmediate,
                   not index_catalog.indisclustered as is_not_clustered,
                   index_catalog.indisvalid,
                   not index_catalog.indcheckxmin as needs_no_xmin_check,
                   index_catalog.indisready,
                   index_catalog.indislive,
                   not index_catalog.indisreplident as is_not_replica_identity,
                   index_catalog.indexprs is null as has_no_expressions,
                   index_catalog.indpred is null as has_no_predicate,
                   operator_class_namespace.nspname::text as operator_class_schema,
                   operator_class.opcname::text as operator_class,
                   index_catalog.indcollation[0] = attribute.attcollation
                       as uses_column_collation,
                   index_catalog.indoption[0] = 0 as has_default_column_options
              from pg_catalog.pg_index index_catalog
              join pg_catalog.pg_class table_relation
                on table_relation.oid = index_catalog.indrelid
              join pg_catalog.pg_namespace table_namespace
                on table_namespace.oid = table_relation.relnamespace
              join pg_catalog.pg_class index_relation
                on index_relation.oid = index_catalog.indexrelid
              join pg_catalog.pg_roles index_owner
                on index_owner.oid = index_relation.relowner
              join pg_catalog.pg_database database
                on database.datname = current_database()
              join pg_catalog.pg_roles database_owner
                on database_owner.oid = database.datdba
              join pg_catalog.pg_am access_method
                on access_method.oid = index_relation.relam
              join pg_catalog.pg_attribute attribute
                on attribute.attrelid = table_relation.oid
               and attribute.attnum = index_catalog.indkey[0]
              join pg_catalog.pg_opclass operator_class
                on operator_class.oid = index_catalog.indclass[0]
              join pg_catalog.pg_namespace operator_class_namespace
                on operator_class_namespace.oid = operator_class.opcnamespace
             where table_namespace.nspname = 'public'
               and table_relation.relname = 'alembic_version'
             order by index_relation.relname
            """
        )
    ).all()
    if [tuple(index) for index in indexes] != [
        (
            "alembic_version_pkc",
            "i",
            "p",
            True,
            True,
            row["database_owner"],
            row["database_owner"],
            "btree",
            1,
            1,
            [1],
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            "pg_catalog",
            "text_ops",
            True,
            True,
        )
    ]:
        return False
    return (
        connection.execute(
            sa.text("select count(*) from public.alembic_version")
        ).scalar_one()
        == 0
    )


def _assert_empty_target() -> None:
    connection = op.get_bind()
    relations = connection.execute(
        sa.text(
            """
            select namespace.nspname::text, relation.relname::text, relation.relkind
              from pg_catalog.pg_class relation
              join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
             where namespace.nspname not like 'pg_%'
               and namespace.nspname <> 'information_schema'
               and relation.relkind in ('r', 'p', 'f', 'S', 'v', 'm', 'c')
             order by namespace.nspname, relation.relname
            """
        )
    ).all()
    expected = [("public", "alembic_version", "r")]
    extra_schemas = (
        connection.execute(
            sa.text(
                """
            select nspname::text
              from pg_catalog.pg_namespace
             where nspname <> 'public'
               and nspname <> 'information_schema'
               and nspname not like 'pg_%'
             order by nspname
            """
            )
        )
        .scalars()
        .all()
    )
    default_acl_count = connection.execute(
        sa.text("select count(*) from pg_catalog.pg_default_acl")
    ).scalar_one()
    non_relation_objects = connection.execute(
        sa.text(
            """
            select object_kind, object_name
              from (
                    select 'type'::text as object_kind, object.typname::text as object_name
                      from pg_catalog.pg_type object
                      join pg_catalog.pg_namespace namespace on namespace.oid = object.typnamespace
                     where namespace.nspname = 'public'
                       and object.typrelid = 0
                       and object.typtype in ('d', 'e', 'r', 'm')
                    union all
                    select 'function', object.proname::text
                      from pg_catalog.pg_proc object
                      join pg_catalog.pg_namespace namespace on namespace.oid = object.pronamespace
                     where namespace.nspname = 'public'
                    union all
                    select 'collation', object.collname::text
                      from pg_catalog.pg_collation object
                      join pg_catalog.pg_namespace namespace on namespace.oid = object.collnamespace
                     where namespace.nspname = 'public'
                    union all
                    select 'conversion', object.conname::text
                      from pg_catalog.pg_conversion object
                      join pg_catalog.pg_namespace namespace on namespace.oid = object.connamespace
                     where namespace.nspname = 'public'
                    union all
                    select 'operator', object.oprname::text
                      from pg_catalog.pg_operator object
                      join pg_catalog.pg_namespace namespace on namespace.oid = object.oprnamespace
                     where namespace.nspname = 'public'
                   ) user_objects
             order by object_kind, object_name
            """
        )
    ).all()
    if (
        [tuple(relation) for relation in relations] != expected
        or extra_schemas
        or default_acl_count
        or non_relation_objects
        or not _verified_version_table(connection)
    ):
        raise RuntimeError(
            "V2 schema initialization requires an empty PostgreSQL database"
        )


def upgrade() -> None:
    runtime_role = _runtime_role()
    quoted_runtime = _quote_identifier(runtime_role)
    connection = op.get_bind()
    _assert_empty_target()

    op.create_table(
        "v2_schema_metadata",
        sa.Column(
            "schema_generation", sa.SmallInteger(), nullable=False, autoincrement=False
        ),
        sa.CheckConstraint(
            "schema_generation = 2",
            name="ck_v2_schema_metadata_schema_generation_v2",
        ),
        sa.PrimaryKeyConstraint("schema_generation", name="pk_v2_schema_metadata"),
    )
    connection.execute(
        sa.text("insert into public.v2_schema_metadata (schema_generation) values (2)")
    )

    connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted_runtime}")
    connection.exec_driver_sql(
        f"GRANT SELECT ON TABLE public.alembic_version, public.v2_schema_metadata TO {quoted_runtime}"
    )
    connection.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted_runtime}"
    )
    connection.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {quoted_runtime}"
    )
    connection.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )


def downgrade() -> None:
    raise RuntimeError("the Track Anywhere V2 schema baseline is irreversible")
