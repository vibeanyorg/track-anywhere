from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backend.tools.backfill_v1.manifest import (
    read_manifest,
    verify_frozen_source,
)


pytestmark = pytest.mark.frozen_dump

EXPECTED_SOURCE_COUNTS = {
    "accounts": 121,
    "postings": 284,
    "transaction_lines": 43,
    "transactions": 135,
}


def _required_path(name: str) -> Path:
    raw = os.environ.get(name)
    if not raw:
        pytest.skip(f"local frozen-dump gate requires {name}")
    path = Path(raw)
    assert path.is_file(), f"{name} must identify an existing regular file"
    return path


def _required_source_url() -> str:
    value = os.environ.get("TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL")
    if not value:
        pytest.skip(
            "local frozen-dump gate requires TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"
        )
    return value


def test_frozen_dump_matches_manifest_and_restored_read_only_source() -> None:
    dump_path = _required_path("TRACK_ANYWHERE_FROZEN_V1_DUMP")
    manifest_path = _required_path("TRACK_ANYWHERE_FROZEN_V1_MANIFEST")
    source_url = _required_source_url()
    manifest = read_manifest(manifest_path)

    engine = create_engine(source_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("show transaction_read_only")).scalar_one()
                == "on"
            )
            revisions = tuple(
                connection.execute(
                    text("select version_num from public.alembic_version")
                ).scalars()
            )
            assert len(revisions) == 1
            revision = str(revisions[0])
            source_counts = {
                table: int(
                    connection.execute(
                        text(f'SELECT count(*) FROM public."{table}"')
                    ).scalar_one()
                )
                for table in EXPECTED_SOURCE_COUNTS
            }
            usdt_scale = connection.execute(
                text("select scale from public.assets where asset_code = 'USDT'")
            ).scalar_one()
            usdt_rows = tuple(
                connection.execute(
                    text(
                        "select id::text, amount "
                        "from public.postings "
                        "where currency = 'USDT' "
                        "and amount <> trunc(amount, 6) "
                        'order by id::text collate "C"'
                    )
                )
            )
    finally:
        engine.dispose()

    verify_frozen_source(
        dump_path=dump_path,
        manifest=manifest,
        actual_source_revision=revision,
    )
    assert source_counts == EXPECTED_SOURCE_COUNTS
    manifest_counts = {item.table: item.row_count for item in manifest.tables}
    if manifest_counts:
        assert {
            table: manifest_counts[table] for table in EXPECTED_SOURCE_COUNTS
        } == EXPECTED_SOURCE_COUNTS
    assert usdt_scale == 8
    assert usdt_rows, "the frozen source must retain its 8-decimal USDT identities"

    identities = tuple(str(row[0]) for row in usdt_rows)
    amounts = tuple(Decimal(row[1]) for row in usdt_rows)
    assert len(identities) == len(set(identities))
    assert all(amount.as_tuple().exponent >= -8 for amount in amounts)
    assert any(amount != amount.quantize(Decimal("0.000001")) for amount in amounts)

    # A stable, secret-free identity digest is suitable for the generated report;
    # no memo or connection information is included.
    identity_digest = hashlib.sha256(
        json.dumps(identities, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert len(identity_digest) == 64
