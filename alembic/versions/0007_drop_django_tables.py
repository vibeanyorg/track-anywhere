"""drop legacy django tables

Revision ID: 0007_drop_django_tables
Revises: 0006_split_memo_purpose
Create Date: 2026-05-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "0007_drop_django_tables"
down_revision: Union[str, Sequence[str], None] = "0006_split_memo_purpose"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_DJANGO_TABLES = {
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "auth_user",
    "auth_user_groups",
    "auth_user_user_permissions",
    "django_admin_log",
    "django_content_type",
    "django_migrations",
    "django_session",
    "django_site",
}
LEGACY_DJANGO_PREFIXES = ("account_", "guardian_", "socialaccount_")
DROP_ORDER = (
    "django_admin_log",
    "account_emailconfirmation",
    "account_emailaddress",
    "socialaccount_socialtoken",
    "socialaccount_socialaccount",
    "socialaccount_socialapp_sites",
    "socialaccount_socialapp",
    "guardian_userobjectpermission",
    "guardian_groupobjectpermission",
    "auth_user_user_permissions",
    "auth_user_groups",
    "auth_group_permissions",
    "auth_user",
    "auth_group",
    "auth_permission",
    "django_session",
    "django_site",
    "django_content_type",
    "django_migrations",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(inspect(bind).get_table_names())
    legacy_tables = {table_name for table_name in existing_tables if _is_legacy_django_table(table_name)}
    ordered_tables = [table_name for table_name in DROP_ORDER if table_name in legacy_tables]
    ordered_tables.extend(sorted(legacy_tables - set(ordered_tables)))
    if not ordered_tables:
        return

    if bind.dialect.name == "postgresql":
        preparer = bind.dialect.identifier_preparer
        for table_name in ordered_tables:
            op.execute(sa.text(f"drop table if exists {preparer.quote(table_name)} cascade"))
        return

    if bind.dialect.name == "sqlite":
        op.execute("pragma foreign_keys=off")
        for table_name in ordered_tables:
            op.execute(f'drop table if exists "{table_name.replace(chr(34), chr(34) + chr(34))}"')
        op.execute("pragma foreign_keys=on")
        return

    for table_name in ordered_tables:
        op.drop_table(table_name)


def downgrade() -> None:
    pass


def _is_legacy_django_table(table_name: str) -> bool:
    return table_name in LEGACY_DJANGO_TABLES or table_name.startswith(LEGACY_DJANGO_PREFIXES)
