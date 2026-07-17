from __future__ import annotations

from collections.abc import Mapping
import json
from types import MappingProxyType
from typing import Final
from uuid import UUID

from track_anywhere.serialization.canonical_json import canonical_json_bytes

from .reference_reducer import ReferenceLedgerFacts


CONTRACT_VERSION: Final = 1
TARGET_BOOK_ID: Final = "a682ddd2-f26a-5ad1-8f0a-f2fc1f75fa3d"
EXPECTED_PLAN_SHA256: Final = (
    "c93ed8aed78918d71caab6e7178a2c4347d72b55a08dfeace1d95eeb81604ec8"
)
EXPECTED_TERMINAL_HASH: Final = (
    "bcc2828422fda617df93fb2fc92e41599f0c694f9f1d502f1dcd22f4d85186fc"
)
COUNT_ALLOWLIST: Final = MappingProxyType(
    {
        "accounts": 121,
        "archives": 1,
        "assets": 20,
        "async_projection_rows": 30,
        "categories": 37,
        "category_versions": 37,
        "credit_card_transactions": 0,
        "descriptions": 138,
        "journal_postings": 290,
        "journal_transactions": 138,
        "ledger_events": 176,
        "quarantine": 0,
        "reporting_lines": 38,
        "reversals": 8,
        "synchronous_projection_applied_events": 176,
    }
)
HASH_ALLOWLIST: Final = frozenset(
    {
        "account_balances_semantic",
        "accounts",
        "assets",
        "async_projection",
        "balances",
        "cards",
        "categories",
        "event_order",
        "event_payloads",
        "events",
        "external_references",
        "journal",
        "journal_postings",
        "journal_transactions",
        "reporting",
        "reversal_semantic",
        "reversals",
        "synchronous_projection",
        "usdt_postings",
    }
)
ROOT_ALLOWLIST: Final = frozenset(
    {
        "archive_id",
        "archive_metadata_hash",
        "archive_plaintext_sha256",
        "book_id",
        "contract_version",
        "counts",
        "description_aggregate_sha256",
        "description_ids",
        "hashes",
        "plan_hash",
        "terminal_hash",
        "terminal_position",
    }
)


class ReferenceArtifactError(ValueError):
    pass


def _fail() -> None:
    raise ReferenceArtifactError("reference_artifact_invalid")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _uuid(value: object) -> str:
    if type(value) is not str:
        _fail()
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        _fail()
    if str(parsed) != value:
        _fail()
    return value


def _sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail()
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        _fail()
    return value


def _artifact_value(reference: ReferenceLedgerFacts) -> dict[str, object]:
    if type(reference) is not ReferenceLedgerFacts:
        _fail()
    return {
        "archive_id": reference.archive_id,
        "archive_metadata_hash": reference.archive_metadata_hash,
        "archive_plaintext_sha256": reference.archive_plaintext_sha256,
        "book_id": reference.book_id,
        "contract_version": CONTRACT_VERSION,
        "counts": dict(reference.counts),
        "description_aggregate_sha256": reference.description_aggregate_sha256,
        "description_ids": list(reference.description_ids),
        "hashes": dict(reference.hashes),
        "plan_hash": reference.plan_hash,
        "terminal_hash": reference.terminal_hash,
        "terminal_position": reference.terminal_position,
    }


def _parse_value(root: Mapping[str, object]) -> ReferenceLedgerFacts:
    if set(root) != ROOT_ALLOWLIST or root.get("contract_version") != CONTRACT_VERSION:
        _fail()
    book_id = _uuid(root.get("book_id"))
    if book_id != TARGET_BOOK_ID:
        _fail()
    terminal_position = root.get("terminal_position")
    if type(terminal_position) is not int or terminal_position != 176:
        _fail()
    plan_hash = _sha256(root.get("plan_hash"))
    terminal_hash = _sha256(root.get("terminal_hash"))
    if plan_hash != EXPECTED_PLAN_SHA256 or terminal_hash != EXPECTED_TERMINAL_HASH:
        _fail()

    raw_counts = _mapping(root.get("counts"))
    if dict(raw_counts) != dict(COUNT_ALLOWLIST):
        _fail()
    counts = {key: int(value) for key, value in raw_counts.items()}

    raw_hashes = _mapping(root.get("hashes"))
    if set(raw_hashes) != HASH_ALLOWLIST:
        _fail()
    hashes = {key: _sha256(value) for key, value in raw_hashes.items()}

    raw_description_ids = root.get("description_ids")
    if type(raw_description_ids) is not list or len(raw_description_ids) != 138:
        _fail()
    description_ids = tuple(_uuid(value) for value in raw_description_ids)
    if tuple(sorted(set(description_ids))) != description_ids:
        _fail()

    return ReferenceLedgerFacts(
        book_id=book_id,
        plan_hash=plan_hash,
        terminal_position=terminal_position,
        terminal_hash=terminal_hash,
        counts=counts,
        hashes=hashes,
        description_ids=description_ids,
        description_aggregate_sha256=_sha256(root.get("description_aggregate_sha256")),
        archive_id=_uuid(root.get("archive_id")),
        archive_plaintext_sha256=_sha256(root.get("archive_plaintext_sha256")),
        archive_metadata_hash=_sha256(root.get("archive_metadata_hash")),
    )


def serialize_reference_artifact(reference: ReferenceLedgerFacts) -> bytes:
    try:
        value = _artifact_value(reference)
        validated = _parse_value(value)
        if validated != reference:
            _fail()
        return canonical_json_bytes(value)
    except ReferenceArtifactError:
        raise
    except (TypeError, ValueError):
        _fail()


def parse_reference_artifact(raw: bytes) -> ReferenceLedgerFacts:
    try:
        if type(raw) is not bytes or not raw:
            _fail()
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
        root = _mapping(parsed)
        reference = _parse_value(root)
        if serialize_reference_artifact(reference) != raw:
            _fail()
        return reference
    except ReferenceArtifactError:
        raise
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        _fail()


__all__ = [
    "CONTRACT_VERSION",
    "COUNT_ALLOWLIST",
    "EXPECTED_PLAN_SHA256",
    "EXPECTED_TERMINAL_HASH",
    "HASH_ALLOWLIST",
    "ReferenceArtifactError",
    "parse_reference_artifact",
    "serialize_reference_artifact",
]
