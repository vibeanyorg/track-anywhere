from __future__ import annotations

import json
import os
from pathlib import Path
from copy import deepcopy

import pytest

from backend.tools.frozen_v1_history.constants import (
    EXPECTED_CREDIT_CARD_REVIEW_SHA256,
)
from backend.tools.frozen_v1_history.credit_card_review import (
    _parse_credit_card_review,
    _read_strict_json,
    calculated_review_sha256,
    parse_approved_credit_card_review,
    read_approved_credit_card_review,
    source_postings_sha256,
    source_row_sha256,
)
from backend.tools.frozen_v1_history.extract import extract_fixed_source
from backend.tools.frozen_v1_history.manifest import read_full_manifest


FIXTURES = Path(__file__).with_name("fixtures")


def test_review_hash_excludes_only_its_content_hash() -> None:
    raw = {
        "schema_version": 1,
        "reviewer": "synthetic-reviewer",
        "content_sha256": "0" * 64,
    }
    expected = calculated_review_sha256(raw)
    raw["content_sha256"] = "f" * 64
    assert calculated_review_sha256(raw) == expected


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
    ],
)
def test_review_json_rejects_duplicates_and_nonfinite_without_echoing_values(
    tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / "review.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="credit-card review"):
        _read_strict_json(path)


def test_review_schema_rejects_extra_fields_without_echoing_them() -> None:
    secret = "sensitive-id-must-not-escape"
    manifest = read_full_manifest(FIXTURES / "frozen_full_manifest.json")
    raw = {"schema_version": 1, "unexpected": secret}

    with pytest.raises(ValueError, match="fields do not match") as exc_info:
        _parse_credit_card_review(
            raw,
            manifest=manifest,
            rows_by_table={},
            expected_content_sha256=EXPECTED_CREDIT_CARD_REVIEW_SHA256,
            expected_summary=(22, 48, 23, 3, 1, 5),
        )

    assert secret not in str(exc_info.value)


def _synthetic_review():
    manifest = read_full_manifest(FIXTURES / "frozen_full_manifest.json")
    accounts = [
        {"book_id": "source-book", "account_id": "card", "type": "liability", "subtype": "credit_card", "currency": "CNY"},
        {"book_id": "source-book", "account_id": "expense", "type": "expense", "subtype": None, "currency": "CNY"},
    ]
    transaction = {"book_id": "source-book", "transaction_id": "tx", "occurred_at": "2026-01-01T00:00:00Z"}
    postings = [
        {"id": "p1", "transaction_id": "tx", "position": 0, "account_id": "card", "currency": "CNY", "amount": "1.00", "amount_semantics": "debit_credit", "side": "credit"},
        {"id": "p2", "transaction_id": "tx", "position": 1, "account_id": "expense", "currency": "CNY", "amount": "1.00", "amount_semantics": "debit_credit", "side": "debit"},
    ]
    rows = {
        "assets": [{"asset_code": "CNY", "scale": 2, "display_scale": 2}],
        "accounts": accounts,
        "transactions": [transaction],
        "postings": postings,
    }
    raw = {
        "schema_version": 1,
        "snapshot_id": manifest.snapshot_id,
        "source_manifest_sha256": manifest.content_sha256,
        "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-07-15T00:00:00Z",
        "transactions": [
            {
                "book_id": "source-book",
                "source_transaction_id": "tx",
                "source_transaction_sha256": source_row_sha256(transaction),
                "source_postings_sha256": source_postings_sha256(postings),
                "postings": [
                    {"source_posting_id": "p1", "target_account_id": "card", "target_side": "credit"},
                    {"source_posting_id": "p2", "target_account_id": "expense", "target_side": "debit"},
                ],
                "post_import_action": "none",
            }
        ],
        "accounts": [],
        "expected_card_balances": [
            {"book_id": "source-book", "source_account_id": "card", "asset_code": "CNY", "natural_units": "100"}
        ],
        "content_sha256": "",
    }
    raw["content_sha256"] = calculated_review_sha256(raw)
    return manifest, rows, raw


def _parse_synthetic(manifest, rows, raw):
    return _parse_credit_card_review(
        raw,
        manifest=manifest,
        rows_by_table=rows,
        expected_content_sha256=raw["content_sha256"],
        expected_summary=(1, 2, 1, 0, 0, 1),
    )


def test_synthetic_core_parser_validates_source_hashes_and_explicit_coverage() -> None:
    manifest, rows, raw = _synthetic_review()
    review = _parse_synthetic(manifest, rows, raw)
    assert review.transaction_count == 1
    assert review.reviewed_posting_count == 2
    assert "synthetic-reviewer" not in repr(review)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("duplicate_transaction", "duplicate|coverage"),
        ("duplicate_posting", "duplicate|coverage"),
        ("partial_posting", "coverage"),
        ("unknown_target", "target is invalid"),
        ("duplicate_balance", "balance coverage"),
        ("bad_timezone", "offset"),
        ("wrong_type", "array"),
        ("source_hash_drift", "source hash"),
    ],
)
def test_synthetic_core_parser_fails_closed_for_semantic_drift(
    mutation: str, match: str
) -> None:
    manifest, rows, raw = _synthetic_review()
    if mutation == "duplicate_transaction":
        raw["transactions"].append(deepcopy(raw["transactions"][0]))
    elif mutation == "duplicate_posting":
        raw["transactions"][0]["postings"].append(
            deepcopy(raw["transactions"][0]["postings"][0])
        )
    elif mutation == "partial_posting":
        raw["transactions"][0]["postings"].pop()
    elif mutation == "unknown_target":
        raw["transactions"][0]["postings"][0]["target_account_id"] = "missing"
    elif mutation == "duplicate_balance":
        raw["expected_card_balances"].append(
            deepcopy(raw["expected_card_balances"][0])
        )
    elif mutation == "bad_timezone":
        raw["reviewed_at"] = "2026-07-15T00:00:00"
    elif mutation == "wrong_type":
        raw["transactions"] = {}
    elif mutation == "source_hash_drift":
        rows["transactions"][0]["occurred_at"] = "2026-01-02T00:00:00Z"
    raw["content_sha256"] = calculated_review_sha256(raw)

    with pytest.raises(ValueError, match=match):
        _parse_synthetic(manifest, rows, raw)


@pytest.mark.skipif(
    not os.getenv("TRACK_ANYWHERE_FROZEN_V1_REVIEW_PATH")
    or not os.getenv("TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"),
    reason="approved review and restored V1 source are exercised on the DO host",
)
def test_real_approved_review_is_fully_hash_and_source_bound() -> None:
    manifest = read_full_manifest(
        Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_MANIFEST_A"])
    )
    source = extract_fixed_source(
        os.environ["TRACK_ANYWHERE_FROZEN_V1_SOURCE_URL"],
        expected_manifest=manifest,
    )
    review = read_approved_credit_card_review(
        Path(os.environ["TRACK_ANYWHERE_FROZEN_V1_REVIEW_PATH"]),
        source=source,
    )

    assert review.content_sha256 == EXPECTED_CREDIT_CARD_REVIEW_SHA256
    assert review.transaction_count == 22
    assert review.reviewed_posting_count == 48
    assert review.card_source_posting_count == 23
    assert review.exact_reversal_action_count == 3
    assert review.account_action_count == 1
    assert review.expected_balance_count == 5
    assert "natural_units" not in repr(review)
