"""Allow audited corrections to payment binding start dates."""

import os
import re

from alembic import op

revision = "v2_0019_binding_backdate"
down_revision = "v2_0018_account_rename"
branch_labels = None
depends_on = None


def _runtime_role() -> str:
    role = os.environ.get("TRACK_ANYWHERE_DB_RUNTIME_ROLE", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise RuntimeError("safe runtime role required")
    return f'"{role}"'


def upgrade() -> None:
    op.execute(
        f"grant update (effective_from) on payment_instrument_bindings to {_runtime_role()}"
    )


def downgrade() -> None:
    op.execute(
        f"revoke update (effective_from) on payment_instrument_bindings from {_runtime_role()}"
    )
