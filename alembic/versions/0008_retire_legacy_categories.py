"""retire legacy transaction categories

Revision ID: 0008_retire_legacy_categories
Revises: 0007_drop_django_tables
Create Date: 2026-05-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic_helpers.retire_legacy_categories import downgrade, upgrade


revision: str = "0008_retire_legacy_categories"
down_revision: Union[str, Sequence[str], None] = "0007_drop_django_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
