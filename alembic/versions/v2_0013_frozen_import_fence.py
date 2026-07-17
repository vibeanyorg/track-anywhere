"""Add the least-privilege frozen-import catalog fence."""

from __future__ import annotations

import os
import re

from alembic import op


revision = "v2_0013_frozen_import_fence"
down_revision = "v2_0012_protected_content"
branch_labels = None
depends_on = None


_RUNTIME_ROLE_ENV = "TRACK_ANYWHERE_DB_RUNTIME_ROLE"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_FUNCTION = "public.v2_acquire_frozen_import_catalog_fence()"


def _runtime_role() -> str:
    value = os.environ.get(_RUNTIME_ROLE_ENV, "")
    if not _IDENTIFIER.fullmatch(value) or len(value.encode("ascii")) > 63:
        raise RuntimeError(
            "TRACK_ANYWHERE_DB_RUNTIME_ROLE is required and must be safe"
        )
    return f'"{value}"'


def upgrade() -> None:
    runtime = _runtime_role()
    op.execute(
        """
        create function public.v2_acquire_frozen_import_catalog_fence()
        returns void language plpgsql security definer
        set search_path = pg_catalog, public as $function$
        begin
            lock table public.assets, public.accounts, public.categories,
                       public.category_versions
            in share row exclusive mode;
        end;
        $function$
        """
    )
    op.execute(f"revoke all privileges on function {_FUNCTION} from public, {runtime}")
    op.execute(f"grant execute on function {_FUNCTION} to {runtime}")


def downgrade() -> None:
    runtime = _runtime_role()
    op.execute(f"revoke all privileges on function {_FUNCTION} from public, {runtime}")
    op.execute(f"drop function {_FUNCTION}")
