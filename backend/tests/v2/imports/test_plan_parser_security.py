from __future__ import annotations

import copy
import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

import pytest

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan, fixture_id
from track_anywhere.application.imports.contracts import (
    canonical_plan_bytes,
    parse_canonical_plan_bytes,
)
from track_anywhere.serialization.canonical_json import (
    EventHashEnvelope,
    canonical_json_bytes,
    event_hash,
)


JSONDict = dict[str, object]
Mutator = Callable[[JSONDict], None]


@lru_cache(maxsize=1)
def _canonical_fixture_bytes() -> bytes:
    return canonical_plan_bytes(build_valid_fixture_plan())


def _event_rows(value: JSONDict) -> list[JSONDict]:
    events = value["events"]
    assert type(events) is list
    assert all(type(event) is dict for event in events)
    return events  # type: ignore[return-value]


def _rehash(value: JSONDict) -> None:
    previous = bytes(32)
    hashes_by_event_id: dict[str, str] = {}
    target_book_id = UUID(str(value["target_book_id"]))
    for event in _event_rows(value):
        event["previous_hash"] = previous.hex()
        payload = event["payload"]
        assert type(payload) is dict
        if event["event_type"] == "JournalTransactionReversed":
            source_hash = hashes_by_event_id.get(str(payload["original_event_id"]))
            if source_hash is not None:
                payload["original_event_hash"] = source_hash
        raw_effective = str(event["effective_at"])
        effective_at = datetime.fromisoformat(raw_effective.replace("Z", "+00:00"))
        calculated = event_hash(
            EventHashEnvelope(
                event_id=UUID(str(event["event_id"])),
                book_id=target_book_id,
                book_position=int(event["book_position"]),
                global_sequence=1,
                recorded_at=datetime(1970, 1, 1, tzinfo=UTC),
                stream_type=str(event["stream_type"]),
                stream_id=UUID(str(event["stream_id"])),
                stream_version=int(event["stream_version"]),
                event_type=str(event["event_type"]),
                event_schema_version=int(event["event_schema_version"]),
                command_id=UUID(str(event["command_id"])),
                actor_subject_id=str(event["actor_subject_id"]),
                correlation_id=UUID(str(event["correlation_id"])),
                causation_event_id=(
                    None
                    if event["causation_event_id"] is None
                    else UUID(str(event["causation_event_id"]))
                ),
                effective_at=effective_at,
                previous_hash=previous,
            ),
            payload,  # type: ignore[arg-type]
        )
        event["event_hash"] = calculated.hex()
        hashes_by_event_id[str(event["event_id"])] = calculated.hex()
        previous = calculated
    value["expected_terminal_hash"] = previous.hex()


def _duplicate_event_id(value: JSONDict) -> None:
    events = _event_rows(value)
    events[1]["event_id"] = events[0]["event_id"]


def _duplicate_stream(value: JSONDict) -> None:
    events = _event_rows(value)
    events[1]["stream_id"] = events[0]["stream_id"]


def _gap_book_position(value: JSONDict) -> None:
    _event_rows(value)[12]["book_position"] = 999


def _wrong_stream_identity(value: JSONDict) -> None:
    _event_rows(value)[7]["stream_id"] = str(fixture_id(99_001))


def _wrong_registry_binding(value: JSONDict) -> None:
    _event_rows(value)[7]["event_type"] = "ReportingLinesAssigned"


def _different_actor(value: JSONDict) -> None:
    for event in _event_rows(value):
        event["actor_subject_id"] = "offline:fixture-other"


def _different_command(value: JSONDict) -> None:
    _event_rows(value)[7]["command_id"] = str(fixture_id(99_002))


def _wrong_correlation(value: JSONDict) -> None:
    _event_rows(value)[7]["correlation_id"] = str(fixture_id(99_003))


def _wrong_causation(value: JSONDict) -> None:
    _event_rows(value)[130]["causation_event_id"] = str(fixture_id(99_004))


def _forward_reversal_reference(value: JSONDict) -> None:
    events = _event_rows(value)
    payload = events[130]["payload"]
    assert type(payload) is dict
    payload["original_event_id"] = events[131]["event_id"]
    payload["original_event_hash"] = events[131]["event_hash"]
    payload["reverses_transaction_id"] = events[131]["stream_id"]
    events[130]["causation_event_id"] = events[131]["event_id"]


def _non_inverse_reversal(value: JSONDict) -> None:
    payload = _event_rows(value)[130]["payload"]
    assert type(payload) is dict
    postings = payload["inverse_postings"]
    assert type(postings) is list
    posting = postings[0]
    assert type(posting) is dict
    posting["side"] = "debit"


def _wrong_posting_count(value: JSONDict) -> None:
    payload = _event_rows(value)[8]["payload"]
    assert type(payload) is dict
    postings = payload["postings"]
    assert type(postings) is list
    del postings[-1]


def _wrong_reporting_count(value: JSONDict) -> None:
    payload = _event_rows(value)[138]["payload"]
    assert type(payload) is dict
    lines = payload["lines"]
    assert type(lines) is list
    extra = copy.deepcopy(lines[0])
    assert type(extra) is dict
    extra["line_id"] = str(fixture_id(99_005))
    extra["line_version_id"] = str(fixture_id(99_006))
    extra["position"] = 1
    lines.append(extra)


def _duplicate_description_reference(value: JSONDict) -> None:
    events = _event_rows(value)
    first_payload = events[0]["payload"]
    second_payload = events[1]["payload"]
    assert type(first_payload) is dict and type(second_payload) is dict
    second_payload["description_ref"] = first_payload["description_ref"]


def _unknown_account_reference(value: JSONDict) -> None:
    payload = _event_rows(value)[0]["payload"]
    assert type(payload) is dict
    postings = payload["postings"]
    assert type(postings) is list and type(postings[0]) is dict
    postings[0]["account_id"] = str(fixture_id(99_007))


def _unknown_category_version(value: JSONDict) -> None:
    payload = _event_rows(value)[138]["payload"]
    assert type(payload) is dict
    lines = payload["lines"]
    assert type(lines) is list and type(lines[0]) is dict
    lines[0]["catalog_id"] = str(fixture_id(99_008))


def _noncanonical_external_reference(value: JSONDict) -> None:
    payload = _event_rows(value)[0]["payload"]
    assert type(payload) is dict
    references = payload["external_references"]
    assert type(references) is list and type(references[0]) is dict
    references[0]["reference"] = "sha256:fixture"


def _wrong_expected_account_balance(value: JSONDict) -> None:
    accounts = value["accounts"]
    assert type(accounts) is list and type(accounts[-1]) is dict
    accounts[-1]["expected_natural_units"] = 999


def _wrong_reviewed_correction_description(value: JSONDict) -> None:
    descriptions = value["descriptions"]
    assert type(descriptions) is list and type(descriptions[135]) is dict
    encoded = descriptions[135]["canonical_plaintext"]
    assert type(encoded) is str
    description = json.loads(base64.urlsafe_b64decode(encoded))
    assert type(description) is dict
    description["purpose"] = "fixture-purpose"
    descriptions[135]["canonical_plaintext"] = base64.urlsafe_b64encode(
        canonical_json_bytes(description)
    ).decode()


def _recompute_account_expectations(value: JSONDict) -> None:
    accounts = value["accounts"]
    assert type(accounts) is list
    raw_balances: dict[str, int] = {}
    for event in _event_rows(value):
        payload = event["payload"]
        assert type(payload) is dict
        if event["event_type"] == "JournalTransactionPosted":
            postings = payload["postings"]
        elif event["event_type"] == "JournalTransactionReversed":
            postings = payload["inverse_postings"]
        else:
            continue
        assert type(postings) is list
        for posting in postings:
            assert type(posting) is dict
            account_id = str(posting["account_id"])
            units = int(posting["units"])
            raw_balances[account_id] = raw_balances.get(account_id, 0) + (
                units if posting["side"] == "debit" else -units
            )
    for account in accounts:
        assert type(account) is dict
        raw = raw_balances.get(str(account["account_id"]), 0)
        account["expected_natural_units"] = (
            -raw
            if account["account_type"] in {"liability", "equity", "income"}
            else raw
        )


def _unbalanced_journal_with_recomputed_expectations(value: JSONDict) -> None:
    payload = _event_rows(value)[20]["payload"]
    assert type(payload) is dict and type(payload["postings"]) is list
    posting = payload["postings"][0]
    assert type(posting) is dict
    posting["units"] = "3"
    _recompute_account_expectations(value)


def _posting_account_asset_mismatch(value: JSONDict) -> None:
    accounts = value["accounts"]
    assert type(accounts) is list and type(accounts[5]) is dict
    accounts[5]["asset_code"] = "T01"


def _reporting_allocation_exceeds_journal(value: JSONDict) -> None:
    payload = _event_rows(value)[138]["payload"]
    assert type(payload) is dict and type(payload["lines"]) is list
    line = payload["lines"][0]
    assert type(line) is dict
    line["units"] = "999999"


def _reporting_line_has_description_reference(value: JSONDict) -> None:
    payload = _event_rows(value)[138]["payload"]
    assert type(payload) is dict and type(payload["lines"]) is list
    line = payload["lines"][0]
    descriptions = value["descriptions"]
    assert type(line) is dict
    assert type(descriptions) is list and type(descriptions[0]) is dict
    line["description_ref"] = descriptions[0]["sidecar_id"]


def _mutate_archive(value: JSONDict, mutation: str) -> None:
    archive = value["archive"]
    assert type(archive) is dict
    encoded = archive["canonical_plaintext"]
    assert type(encoded) is str
    rows = [json.loads(line) for line in base64.urlsafe_b64decode(encoded).splitlines()]
    if mutation == "order":
        rows[1], rows[44] = rows[44], rows[1]
    elif mutation == "binding":
        rows[0]["source_dump_hash"] = "e" * 64
    elif mutation == "table_hash":
        rows[0]["table_hashes"] = {"fixture": "e" * 64}
    elif mutation == "source_payload":
        source = rows[1]["source"]
        assert type(source) is dict
        source["fixture_index"] = 999
    elif mutation == "institution_payload":
        rows[56]["institution"] = {"fixture_index": 999}
    else:
        rows[-1]["reason_code"] = "fixture_changed_reason"
    plaintext = b"\n".join(canonical_json_bytes(row) for row in rows) + b"\n"
    archive["canonical_plaintext"] = base64.urlsafe_b64encode(plaintext).decode()


def _noncanonical_base64(raw: bytes) -> bytes:
    value = json.loads(raw)
    assert type(value) is dict and type(value["archive"]) is dict
    encoded = value["archive"]["canonical_plaintext"]
    assert type(encoded) is str
    value["archive"]["canonical_plaintext"] = encoded + "="
    return canonical_json_bytes(value)


@pytest.mark.parametrize(
    "mutator",
    (
        _duplicate_event_id,
        _duplicate_stream,
        _gap_book_position,
        _wrong_stream_identity,
        _wrong_registry_binding,
        _different_actor,
        _different_command,
        _wrong_correlation,
        _wrong_causation,
        _forward_reversal_reference,
        _non_inverse_reversal,
        _wrong_posting_count,
        _wrong_reporting_count,
        _duplicate_description_reference,
        _unknown_account_reference,
        _unknown_category_version,
        _noncanonical_external_reference,
        _wrong_expected_account_balance,
        _wrong_reviewed_correction_description,
        _unbalanced_journal_with_recomputed_expectations,
        _posting_account_asset_mismatch,
        _reporting_allocation_exceeds_journal,
        _reporting_line_has_description_reference,
    ),
    ids=lambda mutator: mutator.__name__,
)
def test_canonical_parser_rejects_rehashed_topology_tampering(
    mutator: Mutator,
) -> None:
    value = json.loads(_canonical_fixture_bytes())
    assert type(value) is dict
    mutator(value)
    _rehash(value)

    with pytest.raises(ValueError, match="canonical financial history plan"):
        parse_canonical_plan_bytes(canonical_json_bytes(value))


@pytest.mark.parametrize(
    "field",
    ("previous_hash", "event_hash", "expected_terminal_hash"),
)
def test_canonical_parser_rejects_hash_chain_and_terminal_tampering(field: str) -> None:
    value = json.loads(_canonical_fixture_bytes())
    assert type(value) is dict
    if field == "expected_terminal_hash":
        value[field] = "d" * 64
    else:
        _event_rows(value)[75][field] = "d" * 64

    with pytest.raises(ValueError, match="canonical financial history plan"):
        parse_canonical_plan_bytes(canonical_json_bytes(value))


def test_canonical_parser_round_trips_the_exact_payload_registry_union() -> None:
    raw = _canonical_fixture_bytes()
    parsed = parse_canonical_plan_bytes(raw)

    assert canonical_plan_bytes(parsed) == raw
    assert [type(event.payload).__name__ for event in parsed.events] == (
        ["JournalTransactionPosted"] * 130
        + ["JournalTransactionReversed"] * 8
        + ["ReportingLinesAssigned"] * 38
    )


@pytest.mark.parametrize(
    "raw",
    (
        lambda value: b" " + value,
        lambda value: value.replace(
            b"2026-01-02T03:04:05.000006Z",
            b"2026-01-02T11:04:05.000006+08:00",
            1,
        ),
        _noncanonical_base64,
    ),
    ids=("whitespace", "non_utc_equivalent_instant", "noncanonical_base64"),
)
def test_canonical_parser_rejects_alternate_encodings(
    raw: Callable[[bytes], bytes],
) -> None:
    with pytest.raises(ValueError, match="canonical financial history plan"):
        parse_canonical_plan_bytes(raw(_canonical_fixture_bytes()))


@pytest.mark.parametrize(
    "mutation",
    (
        "order",
        "binding",
        "table_hash",
        "source_payload",
        "institution_payload",
        "reason",
    ),
)
def test_canonical_parser_rejects_archive_contract_tampering(mutation: str) -> None:
    value = json.loads(_canonical_fixture_bytes())
    assert type(value) is dict
    _mutate_archive(value, mutation)

    with pytest.raises(ValueError, match="canonical financial history plan"):
        parse_canonical_plan_bytes(canonical_json_bytes(value))
