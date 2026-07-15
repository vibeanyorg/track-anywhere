from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.tools.backfill_v1.reference_reducer import (
    VerificationIssue,
    _verify_credit_cards,
    _verify_reporting,
)


BOOK_ID = "00000000-0000-0000-0000-000000000001"
CHARGE_ID = "00000000-0000-0000-0000-000000000010"
REFUND_ID = "00000000-0000-0000-0000-000000000011"
FIRST_REVERSAL_ID = "00000000-0000-0000-0000-000000000012"
SECOND_REVERSAL_ID = "00000000-0000-0000-0000-000000000013"
CARD_ID = "00000000-0000-0000-0000-000000000020"
EXPENSE_ID = "00000000-0000-0000-0000-000000000021"
ASSET_ID = "00000000-0000-0000-0000-000000000022"
OTHER_EXPENSE_ID = "00000000-0000-0000-0000-000000000023"
OTHER_CARD_ID = "00000000-0000-0000-0000-000000000024"
EFFECTIVE_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _credit_card_relation(
    transaction_id: str,
    *,
    intent: str,
    original_transaction_id: str | None = None,
) -> dict[str, object]:
    return {
        "book_id": BOOK_ID,
        "transaction_id": transaction_id,
        "intent": intent,
        "card_account_id": CARD_ID,
        "counter_account_id": EXPENSE_ID,
        "asset_code": "USD",
        "units": 10_000 if intent == "charge" else 2_500,
        "original_transaction_id": original_transaction_id,
        "source_event_id": f"event-{transaction_id}",
        "source_position": 1 if intent == "charge" else 2,
    }


def _journal_transaction(
    transaction_id: str,
    *,
    kind: str,
    effective_at: datetime,
) -> dict[str, object]:
    return {
        "book_id": BOOK_ID,
        "transaction_id": transaction_id,
        "transaction_kind": kind,
        "effective_at": effective_at,
        "source_event_id": f"event-{transaction_id}",
        "source_position": 1,
    }


def _reversal(
    reversal_transaction_id: str,
    original_transaction_id: str,
) -> dict[str, object]:
    return {
        "book_id": BOOK_ID,
        "reversal_transaction_id": reversal_transaction_id,
        "original_transaction_id": original_transaction_id,
        "source_event_id": f"event-{reversal_transaction_id}",
        "original_event_id": f"event-{original_transaction_id}",
        "original_event_hash": b"x" * 32,
        "reason_code": "user_correction",
    }


def _issue_codes(rows: dict[str, list[dict[str, object]]]) -> set[str]:
    issues: list[VerificationIssue] = []
    _verify_credit_cards(rows, issues)
    return {issue.code for issue in issues}


@pytest.mark.parametrize(
    ("rows", "expected_code"),
    [
        pytest.param(
            {
                "accounts": [],
                "ledger_events": [],
                "credit_card_transactions": [
                    _credit_card_relation(CHARGE_ID, intent="charge")
                ],
                "journal_transactions": [
                    _journal_transaction(
                        CHARGE_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT,
                    ),
                    _journal_transaction(
                        FIRST_REVERSAL_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
                    ),
                    _journal_transaction(
                        SECOND_REVERSAL_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
                    ),
                ],
                "transaction_reversals": [
                    _reversal(FIRST_REVERSAL_ID, CHARGE_ID),
                    _reversal(SECOND_REVERSAL_ID, FIRST_REVERSAL_ID),
                ],
            },
            "credit_card_reversal_chain_forbidden",
            id="reversal-of-credit-card-reversal",
        ),
        pytest.param(
            {
                "accounts": [],
                "ledger_events": [],
                "credit_card_transactions": [
                    _credit_card_relation(CHARGE_ID, intent="charge")
                ],
                "journal_transactions": [
                    _journal_transaction(
                        CHARGE_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT,
                    ),
                    _journal_transaction(
                        FIRST_REVERSAL_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT - timedelta(minutes=1),
                    ),
                ],
                "transaction_reversals": [_reversal(FIRST_REVERSAL_ID, CHARGE_ID)],
            },
            "credit_card_reversal_precedes_source",
            id="credit-card-reversal-before-source",
        ),
        pytest.param(
            {
                "accounts": [],
                "ledger_events": [],
                "credit_card_transactions": [
                    _credit_card_relation(CHARGE_ID, intent="charge"),
                    _credit_card_relation(
                        REFUND_ID,
                        intent="refund",
                        original_transaction_id=CHARGE_ID,
                    ),
                ],
                "journal_transactions": [
                    _journal_transaction(
                        CHARGE_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT,
                    ),
                    _journal_transaction(
                        REFUND_ID,
                        kind="credit_card_refund",
                        effective_at=EFFECTIVE_AT + timedelta(minutes=1),
                    ),
                    _journal_transaction(
                        FIRST_REVERSAL_ID,
                        kind="credit_card_charge",
                        effective_at=EFFECTIVE_AT + timedelta(minutes=2),
                    ),
                ],
                "transaction_reversals": [_reversal(FIRST_REVERSAL_ID, CHARGE_ID)],
            },
            "credit_card_charge_reversed_with_active_refunds",
            id="credit-card-charge-with-active-refund",
        ),
    ],
)
def test_independent_verifier_detects_illegal_credit_card_reversal_topologies(
    rows: dict[str, list[dict[str, object]]],
    expected_code: str,
) -> None:
    assert expected_code in _issue_codes(rows)


def test_independent_verifier_applies_card_guards_to_legacy_generic_history() -> None:
    rows = {
        "accounts": [
            _semantic_account(
                CARD_ID,
                account_type="liability",
                account_subtype="credit_card",
            ),
            _semantic_account(EXPENSE_ID, account_type="expense"),
        ],
        "ledger_events": [],
        "credit_card_transactions": [],
        "journal_transactions": [
            _journal_transaction(
                CHARGE_ID,
                kind="standard",
                effective_at=EFFECTIVE_AT,
            ),
            _journal_transaction(
                FIRST_REVERSAL_ID,
                kind="standard",
                effective_at=EFFECTIVE_AT - timedelta(minutes=1),
            ),
        ],
        "journal_postings": [
            {
                "book_id": BOOK_ID,
                "transaction_id": CHARGE_ID,
                "account_id": EXPENSE_ID,
            },
            {
                "book_id": BOOK_ID,
                "transaction_id": CHARGE_ID,
                "account_id": CARD_ID,
            },
        ],
        "transaction_reversals": [_reversal(FIRST_REVERSAL_ID, CHARGE_ID)],
    }

    assert "credit_card_reversal_precedes_source" in _issue_codes(rows)


def test_independent_verifier_rejects_payment_reporting_event() -> None:
    rows = _valid_typed_rows("payment")
    rows["categories"] = []
    rows["reporting_lines"] = []
    rows["ledger_events"].append(
        {
            "book_id": BOOK_ID,
            "event_id": "event-payment-reporting",
            "event_type": "ReportingLinesAssigned",
            "payload": {
                "transaction_id": REFUND_ID,
                "classification_revision": 1,
                "lines": [],
            },
        }
    )
    issues: list[VerificationIssue] = []

    _verify_reporting(rows, issues)

    assert "credit_card_payment_reporting_forbidden" in {
        issue.code for issue in issues
    }


def _semantic_account(
    account_id: str,
    *,
    account_type: str,
    account_subtype: str | None = None,
) -> dict[str, object]:
    return {
        "book_id": BOOK_ID,
        "account_id": account_id,
        "account_type": account_type,
        "account_subtype": account_subtype,
        "asset_code": "USD",
    }


def _append_typed_transaction(
    rows: dict[str, list[dict[str, object]]],
    *,
    transaction_id: str,
    intent: str,
    counter_account_id: str,
    effective_at: datetime,
    source_position: int,
    card_account_id: str = CARD_ID,
    asset_code: str = "USD",
    units: int = 10_000,
    original_transaction_id: str | None = None,
) -> None:
    if intent in {"charge", "fee"}:
        first_account_id, second_account_id = counter_account_id, card_account_id
    else:
        first_account_id, second_account_id = card_account_id, counter_account_id
    event_id = f"event-{transaction_id}"
    rows["ledger_events"].append(
        {
            "book_id": BOOK_ID,
            "event_id": event_id,
            "book_position": source_position,
            "event_type": "CreditCardTransactionRecorded",
            "effective_at": effective_at,
            "payload": {
                "intent": intent,
                "transaction_id": transaction_id,
                "card_account_id": card_account_id,
                "counter_account_id": counter_account_id,
                "original_transaction_id": original_transaction_id,
                "postings": [
                    {
                        "posting_id": f"posting-{transaction_id}-0",
                        "position": 0,
                        "account_id": first_account_id,
                        "asset_code": asset_code,
                        "side": "debit",
                        "units": str(units),
                    },
                    {
                        "posting_id": f"posting-{transaction_id}-1",
                        "position": 1,
                        "account_id": second_account_id,
                        "asset_code": asset_code,
                        "side": "credit",
                        "units": str(units),
                    },
                ],
            },
        }
    )
    rows["journal_postings"].extend(
        {
            "book_id": BOOK_ID,
            "transaction_id": transaction_id,
            "posting_id": f"posting-{transaction_id}-{position}",
            "posting_position": position,
            "account_id": account_id,
            "asset_code": asset_code,
            "side": side,
            "units": units,
        }
        for position, (account_id, side) in enumerate(
            (
                (first_account_id, "debit"),
                (second_account_id, "credit"),
            )
        )
    )
    rows["credit_card_transactions"].append(
        {
            "book_id": BOOK_ID,
            "transaction_id": transaction_id,
            "intent": intent,
            "card_account_id": card_account_id,
            "counter_account_id": counter_account_id,
            "asset_code": asset_code,
            "units": units,
            "original_transaction_id": original_transaction_id,
            "source_event_id": event_id,
            "source_position": source_position,
        }
    )
    rows["journal_transactions"].append(
        _journal_transaction(
            transaction_id,
            kind=f"credit_card_{intent}",
            effective_at=effective_at,
        )
    )


def _valid_typed_rows(intent: str) -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = {
        "accounts": [
            _semantic_account(
                CARD_ID,
                account_type="liability",
                account_subtype="credit_card",
            ),
            _semantic_account(EXPENSE_ID, account_type="expense"),
            _semantic_account(ASSET_ID, account_type="asset"),
        ],
        "ledger_events": [],
        "credit_card_transactions": [],
        "journal_transactions": [],
        "journal_postings": [],
        "transaction_reversals": [],
    }
    if intent == "refund":
        _append_typed_transaction(
            rows,
            transaction_id=CHARGE_ID,
            intent="charge",
            counter_account_id=EXPENSE_ID,
            effective_at=EFFECTIVE_AT,
            source_position=1,
        )
        _append_typed_transaction(
            rows,
            transaction_id=REFUND_ID,
            intent="refund",
            counter_account_id=EXPENSE_ID,
            effective_at=EFFECTIVE_AT + timedelta(minutes=1),
            source_position=2,
            units=2_500,
            original_transaction_id=CHARGE_ID,
        )
    else:
        transaction_id = CHARGE_ID if intent == "charge" else REFUND_ID
        _append_typed_transaction(
            rows,
            transaction_id=transaction_id,
            intent=intent,
            counter_account_id=ASSET_ID if intent == "payment" else EXPENSE_ID,
            effective_at=EFFECTIVE_AT,
            source_position=1,
        )
    return rows


def _typed_event(
    rows: dict[str, list[dict[str, object]]], intent: str
) -> dict[str, object]:
    return next(
        event
        for event in rows["ledger_events"]
        if isinstance(event["payload"], dict) and event["payload"]["intent"] == intent
    )


def _typed_relation(
    rows: dict[str, list[dict[str, object]]], intent: str
) -> dict[str, object]:
    return next(
        relation
        for relation in rows["credit_card_transactions"]
        if relation["intent"] == intent
    )


@pytest.mark.parametrize("intent", ["charge", "payment", "refund", "fee"])
@pytest.mark.parametrize("corruption", ["accounts", "sides"])
def test_independent_verifier_rejects_consistently_wrong_typed_event_legs(
    intent: str,
    corruption: str,
) -> None:
    rows = _valid_typed_rows(intent)
    assert _issue_codes(rows) == set()
    event = _typed_event(rows, intent)
    payload = event["payload"]
    assert isinstance(payload, dict)
    postings = payload["postings"]
    assert isinstance(postings, list)
    target_transaction_id = payload["transaction_id"]
    projected = [
        posting
        for posting in rows["journal_postings"]
        if posting["transaction_id"] == target_transaction_id
    ]
    if corruption == "accounts":
        postings[0]["account_id"], postings[1]["account_id"] = (
            postings[1]["account_id"],
            postings[0]["account_id"],
        )
        projected[0]["account_id"], projected[1]["account_id"] = (
            projected[1]["account_id"],
            projected[0]["account_id"],
        )
    else:
        postings[0]["side"] = "credit"
        postings[1]["side"] = "debit"
        projected[0]["side"] = "credit"
        projected[1]["side"] = "debit"

    assert "credit_card_event_posting_semantics_invalid" in _issue_codes(rows)


@pytest.mark.parametrize(
    ("corruption", "expected_code"),
    [
        pytest.param(
            "one-leg",
            "credit_card_event_posting_count_invalid",
            id="one-leg",
        ),
        pytest.param(
            "three-legs",
            "credit_card_event_posting_count_invalid",
            id="three-legs",
        ),
        pytest.param(
            "unequal-units",
            "credit_card_event_posting_amount_invalid",
            id="unequal-units",
        ),
        pytest.param(
            "unequal-assets",
            "credit_card_event_posting_asset_invalid",
            id="unequal-assets",
        ),
    ],
)
def test_independent_verifier_rejects_corrupt_typed_event_legs(
    corruption: str,
    expected_code: str,
) -> None:
    rows = _valid_typed_rows("charge")
    assert _issue_codes(rows) == set()
    event = _typed_event(rows, "charge")
    payload = event["payload"]
    assert isinstance(payload, dict)
    postings = payload["postings"]
    assert isinstance(postings, list)
    if corruption == "one-leg":
        postings.pop()
    elif corruption == "three-legs":
        postings.append(
            {
                "posting_id": "posting-extra",
                "position": 2,
                "account_id": EXPENSE_ID,
                "asset_code": "USD",
                "side": "debit",
                "units": "10000",
            }
        )
    elif corruption == "unequal-units":
        postings[1]["units"] = "9999"
    else:
        postings[1]["asset_code"] = "EUR"

    assert expected_code in _issue_codes(rows)


def test_independent_verifier_rejects_refund_with_different_expense_counter() -> None:
    rows = _valid_typed_rows("refund")
    assert _issue_codes(rows) == set()
    rows["accounts"].append(_semantic_account(OTHER_EXPENSE_ID, account_type="expense"))
    event = _typed_event(rows, "refund")
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["counter_account_id"] = OTHER_EXPENSE_ID
    postings = payload["postings"]
    assert isinstance(postings, list)
    postings[1]["account_id"] = OTHER_EXPENSE_ID
    _typed_relation(rows, "refund")["counter_account_id"] = OTHER_EXPENSE_ID

    assert "credit_card_refund_source_invalid" in _issue_codes(rows)


def test_independent_verifier_rejects_refund_before_original_charge() -> None:
    rows = _valid_typed_rows("refund")
    assert _issue_codes(rows) == set()
    early = EFFECTIVE_AT - timedelta(microseconds=1)
    _typed_event(rows, "refund")["effective_at"] = early
    refund_transaction = next(
        transaction
        for transaction in rows["journal_transactions"]
        if transaction["transaction_id"] == REFUND_ID
    )
    refund_transaction["effective_at"] = early

    assert "credit_card_refund_precedes_source" in _issue_codes(rows)


@pytest.mark.parametrize("dimension", ["card", "asset"])
def test_independent_verifier_requires_refund_to_match_charge_card_and_asset(
    dimension: str,
) -> None:
    rows = _valid_typed_rows("refund")
    assert _issue_codes(rows) == set()
    relation = _typed_relation(rows, "refund")
    if dimension == "card":
        relation["card_account_id"] = OTHER_CARD_ID
    else:
        relation["asset_code"] = "EUR"

    assert "credit_card_refund_source_invalid" in _issue_codes(rows)


def test_independent_verifier_enforces_active_refund_limit() -> None:
    rows = _valid_typed_rows("refund")
    assert _issue_codes(rows) == set()
    event = _typed_event(rows, "refund")
    payload = event["payload"]
    assert isinstance(payload, dict)
    postings = payload["postings"]
    assert isinstance(postings, list)
    postings[0]["units"] = "10001"
    postings[1]["units"] = "10001"
    _typed_relation(rows, "refund")["units"] = 10_001

    assert "credit_card_refund_exceeded" in _issue_codes(rows)
