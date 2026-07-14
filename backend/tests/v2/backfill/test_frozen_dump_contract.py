from __future__ import annotations

from collections.abc import Mapping
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


def _classification_state(value: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(value["category_id"]),
        str(value["category_version_id"]),
        json.dumps(value.get("category_path_snapshot"), sort_keys=True),
    )


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
            line_shape = (
                connection.execute(
                    text(
                        "select count(*) as total, "
                        "count(*) filter (where category_id is not null) as categorized, "
                        "count(*) filter (where category_id is null) as historical, "
                        "count(*) filter (where counterparty_id is not null) as counterparties, "
                        "count(*) filter (where project_id is not null) as projects, "
                        "count(*) filter (where necessity is distinct from 'unknown') as necessities, "
                        "count(*) filter (where reimbursement_status is distinct from 'none') as reimbursements "
                        "from public.transaction_lines"
                    )
                )
                .mappings()
                .one()
            )
            historical_line_types = dict(
                connection.execute(
                    text(
                        "select line_type, count(*) from public.transaction_lines "
                        "where category_id is null group by line_type"
                    )
                ).tuples()
            )
            classification_rows = tuple(
                connection.execute(
                    text(
                        "select classification_event_id::text, book_id::text, event_type, "
                        "before, after, created_at from public.classification_events "
                        'order by book_id::text collate "C", created_at, '
                        'classification_event_id::text collate "C"'
                    )
                ).mappings()
            )
            current_lines = {
                (str(row.transaction_id), str(row.line_id)): row
                for row in connection.execute(
                    text(
                        "select transaction_id::text, line_id::text, category_id::text, "
                        "category_version_id::text, category_path_snapshot "
                        "from public.transaction_lines"
                    )
                )
            }
            investment_counts = connection.execute(
                text(
                    "select (select count(*) from public.investment_events) as events, "
                    "(select count(*) from public.investment_valuations) as valuations"
                )
            ).one()
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
    assert dict(line_shape) == {
        "categorized": 38,
        "counterparties": 0,
        "historical": 5,
        "necessities": 0,
        "projects": 0,
        "reimbursements": 0,
        "total": 43,
    }
    assert historical_line_types == {"fx_exchange": 4, "fx_fee": 1}
    assert (investment_counts.events, investment_counts.valuations) == (6, 0)
    assert len(classification_rows) == 43
    assert sum(row.event_type == "create" for row in classification_rows) == 35
    reclassifications = [
        row for row in classification_rows if row.event_type == "reclassify"
    ]
    assert len(reclassifications) == 8
    chains: dict[tuple[str, str], list[object]] = {}
    for row in reclassifications:
        before, after = row.before, row.after
        for snapshot in (before, after):
            assert all(
                isinstance(snapshot.get(field), str) and snapshot[field]
                for field in (
                    "transaction_id",
                    "line_id",
                    "category_id",
                    "category_version_id",
                )
            )
        chains.setdefault(
            (str(after["transaction_id"]), str(after["line_id"])), []
        ).append(row)
    for line_key, events in chains.items():
        prior_after = None
        prior_transition = None
        for event in events:
            transition = (
                _classification_state(event.before),
                _classification_state(event.after),
            )
            if prior_after is not None:
                assert transition[0] == prior_after or transition == prior_transition
            prior_after = transition[1]
            prior_transition = transition
        current = current_lines[line_key]
        assert prior_after == (
            str(current.category_id),
            str(current.category_version_id),
            json.dumps(current.category_path_snapshot, sort_keys=True),
        )

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
