from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.tests.v2.contract.test_v2_journal_api import (
    _financial_headers,
    _journal_client,
    _post_path,
    _post_payload,
    _seed_authenticated_journal,
)
from track_anywhere.infrastructure.db.models.event_store import CommandReceiptRecord


def _receipt_count(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(CommandReceiptRecord))


def test_same_key_replays_stably_and_changed_payload_conflicts(pg_engine) -> None:
    scenario = _seed_authenticated_journal(pg_engine)
    client = _journal_client(pg_engine)
    headers = _financial_headers("stable-replay-key")
    payload = _post_payload(scenario)

    first = client.post(_post_path(scenario), headers=headers, json=payload)
    replay = client.post(_post_path(scenario), headers=headers, json=payload)

    assert (first.status_code, replay.status_code) == (201, 201)
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert _receipt_count(pg_engine) == 1

    changed = _post_payload(scenario, amount="12.35")
    conflict = client.post(_post_path(scenario), headers=headers, json=changed)

    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency key conflict"}
    assert "receipt" not in conflict.text.lower()
    assert "stable-replay-key" not in conflict.text
    assert _receipt_count(pg_engine) == 1


def test_unauthorized_request_does_not_create_or_reveal_a_receipt(pg_engine) -> None:
    scenario = _seed_authenticated_journal(pg_engine)
    client = _journal_client(pg_engine)
    raw_key = "unauthorized-secret-key"

    response = client.post(
        _post_path(scenario),
        headers={"X-Idempotency-Key": raw_key},
        json=_post_payload(scenario),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication is required"}
    assert "receipt" not in response.text.lower()
    assert raw_key not in response.text
    assert _receipt_count(pg_engine) == 0


def test_financial_write_requires_an_idempotency_key(pg_engine) -> None:
    scenario = _seed_authenticated_journal(pg_engine)
    client = _journal_client(pg_engine)

    response = client.post(
        _post_path(scenario),
        headers={"X-API-Key": "ta_journal_contract"},
        json=_post_payload(scenario),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "X-Idempotency-Key is required"}
    assert _receipt_count(pg_engine) == 0
