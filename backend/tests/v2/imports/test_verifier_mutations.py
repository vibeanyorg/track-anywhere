from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re

import pytest

from backend.tests.v2.imports._plan_factory import build_valid_fixture_plan
from backend.tools.frozen_v1_history.reference_reducer import (
    ReferenceReductionError,
    reduce_canonical_plan,
)
from backend.tools.frozen_v1_history.verify import (
    FrozenHistoryObservation,
    verify_frozen_history,
)
from track_anywhere.application.imports.contracts import canonical_plan_bytes
from track_anywhere.verification import LedgerReadbackFacts


_EVENT_HASH_DOMAIN = b"track-anywhere:v2:ledger-event-hash:sha256:v1"


def _raw_plan() -> dict[str, object]:
    raw = json.loads(canonical_plan_bytes(build_valid_fixture_plan()))
    assert type(raw) is dict
    return raw


def _object(value: object) -> dict[str, object]:
    assert type(value) is dict
    return value


def _list(value: object) -> list[object]:
    assert type(value) is list
    return value


def _events(raw: dict[str, object]) -> list[dict[str, object]]:
    return [_object(value) for value in _list(raw["events"])]


def _event(raw: dict[str, object], event_type: str) -> dict[str, object]:
    return next(value for value in _events(raw) if value["event_type"] == event_type)


def _payload(event: dict[str, object]) -> dict[str, object]:
    return _object(event["payload"])


def _reseal_event_chain(raw: dict[str, object]) -> None:
    """Repair only integrity fields derived from a logical raw-fact mutation."""

    previous_hash = "0" * 64
    book_id = raw["target_book_id"]
    assert type(book_id) is str
    for event in _events(raw):
        event["previous_hash"] = previous_hash
        envelope = {
            "actor_subject_id": event["actor_subject_id"],
            "book_id": book_id,
            "book_position": event["book_position"],
            "causation_event_id": event["causation_event_id"],
            "command_id": event["command_id"],
            "correlation_id": event["correlation_id"],
            "effective_at": event["effective_at"],
            "event_id": event["event_id"],
            "event_schema_version": event["event_schema_version"],
            "event_type": event["event_type"],
            "previous_hash": previous_hash,
            "stream_id": event["stream_id"],
            "stream_type": event["stream_type"],
            "stream_version": event["stream_version"],
        }
        event_hash = hashlib.sha256(
            _EVENT_HASH_DOMAIN
            + b"\0"
            + json.dumps(
                envelope,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            + b"\0"
            + json.dumps(
                event["payload"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        event["event_hash"] = event_hash
        previous_hash = event_hash
    raw["expected_terminal_hash"] = previous_hash


def _journal_event(raw: dict[str, object]) -> dict[str, object]:
    reversed_event_ids = {
        _payload(event)["original_event_id"]
        for event in _events(raw)
        if event["event_type"] == "JournalTransactionReversed"
    }
    return next(
        event
        for event in _events(raw)
        if event["event_type"] == "JournalTransactionPosted"
        and event["event_id"] not in reversed_event_ids
    )


def _posting(event: dict[str, object], position: int = 0) -> dict[str, object]:
    return _object(_list(_payload(event)["postings"])[position])


def _different_account_for_posting(
    raw: dict[str, object], posting: dict[str, object]
) -> str:
    return next(
        str(account["account_id"])
        for account in map(_object, _list(raw["accounts"]))
        if account["account_id"] != posting["account_id"]
        and account["asset_code"] == posting["asset_code"]
    )


def _mutate_raw_fact(raw: dict[str, object], mutation: str) -> None:
    if mutation in {
        "posting_side",
        "posting_units",
        "posting_account",
        "posting_asset",
    }:
        posting = _posting(_journal_event(raw))
        if mutation == "posting_side":
            posting["side"] = "credit"
        elif mutation == "posting_units":
            posting["units"] = str(int(str(posting["units"])) + 1)
        elif mutation == "posting_account":
            posting["account_id"] = _different_account_for_posting(raw, posting)
        else:
            posting["asset_code"] = "T01"
        _reseal_event_chain(raw)
        return

    if mutation == "event_order":
        events = _list(raw["events"])
        events[0], events[1] = events[1], events[0]
        return
    if mutation == "stream_version":
        _events(raw)[0]["stream_version"] = 2
        _reseal_event_chain(raw)
        return
    if mutation == "event_hash":
        _events(raw)[0]["event_hash"] = "f" * 64
        return
    if mutation == "terminal_hash":
        raw["expected_terminal_hash"] = "0" * 64
        return

    if mutation in {"reversal_source_id", "reversal_source_hash"}:
        reversal = _payload(_event(raw, "JournalTransactionReversed"))
        if mutation == "reversal_source_id":
            reversal["original_event_id"] = "00000000-0000-4000-8000-000000000001"
        else:
            reversal["original_event_hash"] = "0" * 64
        _reseal_event_chain(raw)
        return

    if mutation in {"reporting_category", "reporting_version"}:
        line = _object(
            _list(_payload(_event(raw, "ReportingLinesAssigned"))["lines"])[0]
        )
        categories = [_object(value) for value in _list(raw["categories"])]
        other = next(
            category
            for category in categories
            if category["category_id"] != line["dimension_id"]
        )
        if mutation == "reporting_category":
            line["dimension_id"] = other["category_id"]
        else:
            line["catalog_id"] = other["current_version_id"]
        _reseal_event_chain(raw)
        return

    cards = [
        account
        for account in map(_object, _list(raw["accounts"]))
        if account["account_subtype"] == "credit_card"
    ]
    if mutation == "card_natural_balance":
        cards[0]["expected_natural_units"] = 1
        return
    if mutation == "retired_alias_state":
        alias = next(account for account in cards if account["close_after_import"])
        alias["close_after_import"] = False
        return
    if mutation == "eight_decimal_usdt":
        usdt_event = next(
            event
            for event in _events(raw)
            if event["event_type"] == "JournalTransactionPosted"
            and any(
                posting["asset_code"] == "USDT"
                for posting in map(
                    _object,
                    _list(_payload(event)["postings"]),
                )
            )
        )
        usdt_posting = next(
            posting
            for posting in map(_object, _list(_payload(usdt_event)["postings"]))
            if posting["asset_code"] == "USDT"
        )
        assert usdt_posting["units"] == "12345678"
        usdt_posting["units"] = "12345679"
        _reseal_event_chain(raw)
        return
    raise AssertionError(f"unknown mutation: {mutation}")


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    (
        ("posting_side", "journal_not_balanced"),
        ("posting_units", "journal_not_balanced"),
        ("posting_account", "account_natural_balance_mismatch"),
        ("posting_asset", "posting_account_asset_mismatch"),
        ("event_order", "event_order_mismatch"),
        ("stream_version", "event_version_mismatch"),
        ("event_hash", "event_hash_mismatch"),
        ("terminal_hash", "terminal_hash_mismatch"),
        ("reversal_source_id", "reversal_source_mismatch"),
        ("reversal_source_hash", "reversal_source_mismatch"),
        ("reporting_category", "reporting_category_version_mismatch"),
        ("reporting_version", "reporting_category_version_mismatch"),
        ("card_natural_balance", "account_natural_balance_mismatch"),
        ("retired_alias_state", "card_account_contract_mismatch"),
        ("eight_decimal_usdt", "journal_not_balanced"),
    ),
)
def test_reference_reducer_rejects_each_real_raw_fact_mutation(
    mutation: str,
    error_code: str,
) -> None:
    raw = _raw_plan()
    _mutate_raw_fact(raw, mutation)

    with pytest.raises(ReferenceReductionError, match=f"^{error_code}$"):
        reduce_canonical_plan(raw)


def test_success_report_contains_only_counts_and_hashes_not_private_facts() -> None:
    reference = reduce_canonical_plan(_raw_plan())
    observation = _matching_observation(reference)

    report = verify_frozen_history(reference, observation)
    rendered = json.dumps(report.to_dict(), sort_keys=True)

    assert report.status == "PASS"
    assert report.issues == ()
    assert set(report.to_dict()) == {"counts", "hashes", "issues", "status"}
    assert "fixture-purpose" not in rendered
    assert "canonical_plaintext" not in rendered
    assert "description_ids" not in rendered
    assert set(report.hashes) == {
        "account_balances_semantic",
        "accounts",
        "archive_metadata",
        "archive_plaintext",
        "archive_seal",
        "assets",
        "async_projection",
        "balances",
        "cards",
        "categories",
        "description_aggregate",
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
        "terminal",
        "usdt_postings",
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in report.hashes.values())


def _matching_observation(reference) -> FrozenHistoryObservation:
    ledger_counts = {
        key: value
        for key, value in reference.counts.items()
        if key not in {"archives", "descriptions", "quarantine"}
    }
    return FrozenHistoryObservation(
        ledger=LedgerReadbackFacts(
            book_id=reference.book_id,
            terminal_position=reference.terminal_position,
            terminal_hash=reference.terminal_hash,
            counts=ledger_counts,
            hashes=reference.hashes,
            async_checkpoint_position=reference.terminal_position,
            unresolved_projection_failures=0,
        ),
        additional_counts={"archives": 1, "descriptions": 138, "quarantine": 0},
        description_aggregate_sha256=reference.description_aggregate_sha256,
        archive_plaintext_sha256=reference.archive_plaintext_sha256,
        archive_metadata_hash=reference.archive_metadata_hash,
        archive_seal="a" * 64,
        archive_verified=True,
    )


def test_unknown_observed_digest_is_rejected_without_echoing_its_key_or_value() -> None:
    reference = reduce_canonical_plan(_raw_plan())
    observation = _matching_observation(reference)
    injected_key = "future_private_digest_name"
    injected_value = "b" * 64
    observation = replace(
        observation,
        ledger=replace(
            observation.ledger,
            hashes={**observation.ledger.hashes, injected_key: injected_value},
        ),
    )

    report = verify_frozen_history(reference, observation)
    rendered = json.dumps(report.to_dict(), sort_keys=True)

    assert report.status == "FAIL"
    assert "unexpected_digest" in report.issues
    assert injected_key not in rendered
    assert injected_value not in rendered


def test_unknown_observed_count_is_rejected_without_echoing_its_key_or_value() -> None:
    reference = reduce_canonical_plan(_raw_plan())
    observation = _matching_observation(reference)
    injected_key = "private_memo:SECRET"
    injected_value = 987654321
    observation = replace(
        observation,
        ledger=replace(
            observation.ledger,
            counts={**observation.ledger.counts, injected_key: injected_value},
        ),
    )

    report = verify_frozen_history(reference, observation)
    rendered = json.dumps(report.to_dict(), sort_keys=True)

    assert report.status == "FAIL"
    assert "unexpected_count" in report.issues
    assert set(report.counts).issubset(reference.counts)
    assert injected_key not in rendered
    assert str(injected_value) not in rendered


@pytest.mark.parametrize("invalid_value", (-1, True))
def test_invalid_observed_count_is_rejected_and_omitted(invalid_value: object) -> None:
    reference = reduce_canonical_plan(_raw_plan())
    observation = _matching_observation(reference)
    observed_counts = dict(observation.ledger.counts)
    observed_counts["journal_transactions"] = invalid_value  # type: ignore[assignment]
    observation = replace(
        observation,
        ledger=replace(observation.ledger, counts=observed_counts),
    )

    report = verify_frozen_history(reference, observation)

    assert report.status == "FAIL"
    assert "invalid_count" in report.issues
    assert "journal_transactions" not in report.counts
    assert all(type(value) is int and value >= 0 for value in report.counts.values())


def test_invalid_digest_is_rejected_and_omitted_from_the_secret_free_report() -> None:
    reference = reduce_canonical_plan(_raw_plan())
    observation = _matching_observation(reference)
    invalid_value = "A" * 64
    observed_hashes = dict(observation.ledger.hashes)
    observed_hashes["journal"] = invalid_value
    observation = replace(
        observation,
        ledger=replace(observation.ledger, hashes=observed_hashes),
    )

    report = verify_frozen_history(reference, observation)
    rendered = json.dumps(report.to_dict(), sort_keys=True)

    assert report.status == "FAIL"
    assert "invalid_digest" in report.issues
    assert "journal_digest_mismatch" in report.issues
    assert "journal" not in report.hashes
    assert invalid_value not in rendered
