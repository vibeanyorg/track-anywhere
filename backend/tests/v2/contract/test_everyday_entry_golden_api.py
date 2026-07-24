from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.everyday_entries import (
    ACTOR_ID,
    BOOK_ID,
    GoldenEntryScenario,
    golden_scenarios,
    seed_golden_book,
)
from track_anywhere.api.errors import install_error_handlers
from track_anywhere.api.v2.entries import create_entry_router
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.crypto import (
    DuplicateDetectionKeyProvider,
    ProtectedContentCipher,
    ProtectedContentKeyring,
)
from track_anywhere.infrastructure.db.models.auth import CredentialRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


RAW_API_KEY = "ta_everyday_golden_api"


def _client(pg_engine) -> TestClient:
    seed_golden_book(pg_engine)
    now = datetime.now(UTC)
    with Session(pg_engine) as session, session.begin():
        session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=sha256(RAW_API_KEY.encode()).digest(),
                jti=uuid4(),
                actor_subject_id=ACTOR_ID,
                actor_type="human",
                auth_kind="api_key",
                book_id=BOOK_ID,
                scopes=["ledger:read", "ledger:write"],
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
                revoked_at=None,
                last_used_at=None,
            )
        )
    factory = sessionmaker(pg_engine, expire_on_commit=False)

    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    app = FastAPI()
    app.state.public_base_url = "http://testserver"
    app.include_router(
        create_entry_router(
            get_session=get_session,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            ledger_committer=LedgerCommitter(),
            protected_content_cipher=ProtectedContentCipher(
                ProtectedContentKeyring.from_mapping(
                    active_key_ref="golden-v1",
                    keys={"golden-v1": b"p" * 32},
                )
            ),
            duplicate_key_provider=DuplicateDetectionKeyProvider(bytes(range(32))),
        ),
        prefix="/api/v2",
    )
    install_error_handlers(app)
    return TestClient(app)


def _prepare_commit_receipt(
    client: TestClient,
    scenario: GoldenEntryScenario,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    prepare_path = f"/api/v2/books/{BOOK_ID}/entries/prepare"
    prepared_response = client.post(
        prepare_path,
        json=scenario.entry.model_dump(mode="json"),
        headers={"X-API-Key": RAW_API_KEY},
    )
    assert prepared_response.status_code == 200
    prepared = prepared_response.json()
    assert prepared["status"] == "ready"
    assert prepared["commit_token"]
    assert prepared["preview"]["amount"] == {
        "value": scenario.expected_value,
        "asset_code": "CNY",
        "display": f"{scenario.expected_value} CNY",
    }
    assert tuple(prepared["resolved"]["category_ids"]) == tuple(
        str(value) for value in scenario.expected_categories
    )

    request_id = uuid4()
    commit_payload = {
        "intent_id": prepared["intent_id"],
        "commit_token": prepared["commit_token"],
        "request_id": str(request_id),
    }
    committed_response = client.post(
        f"/api/v2/books/{BOOK_ID}/entries/commit",
        json=commit_payload,
        headers={
            "X-API-Key": RAW_API_KEY,
            "X-Idempotency-Key": str(request_id),
        },
    )
    assert committed_response.status_code == 201
    committed = committed_response.json()
    assert committed["status"] == "committed"
    assert committed["intent_id"] == prepared["intent_id"]
    assert committed["request_id"] == str(request_id)
    assert committed["replayed"] is False
    assert committed["preview"] == prepared["preview"]

    receipt_response = client.get(
        f"/api/v2/books/{BOOK_ID}/entries/{committed['transaction_id']}",
        headers={"X-API-Key": RAW_API_KEY},
    )
    assert receipt_response.status_code == 200
    receipt = receipt_response.json()
    assert receipt["transaction_id"] == committed["transaction_id"]
    assert receipt["amount"] == {
        "value": scenario.expected_value,
        "asset_code": "CNY",
        "scale": 2,
    }
    assert tuple(
        item["category_id"] for item in receipt["category_allocations"]
    ) == tuple(str(value) for value in scenario.expected_categories)
    assert receipt["raw_journal"]["transaction_kind"] == (
        scenario.expected_financial_kind
    )
    assert receipt["narrative"]["status"] == "redacted"
    return prepared, committed, receipt


def test_rest_golden_prepare_commit_and_receipt_contracts(pg_engine) -> None:
    client = _client(pg_engine)
    committed: dict[str, dict[str, object]] = {}

    for scenario in golden_scenarios():
        _, result, receipt = _prepare_commit_receipt(client, scenario)
        committed[scenario.name] = result
        if scenario.expected_financial_kind == "credit_card_payment":
            assert receipt["category_allocations"] == []
            assert receipt["category_availability"] == "not_applicable"
            assert receipt["source_account"]["account_id"] == str(
                scenario.expected_postings[1][0]
            )
            assert receipt["target_account"]["account_id"] == str(
                scenario.expected_postings[0][0]
            )

    duplicate = client.post(
        f"/api/v2/books/{BOOK_ID}/entries/prepare",
        json=golden_scenarios()[0].entry.model_dump(mode="json"),
        headers={"X-API-Key": RAW_API_KEY},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate_suspected"
    assert duplicate.json()["commit_token"] is None
    assert duplicate.json()["clarifications"][0]["code"] == (
        "duplicate_confirmation"
    )
    assert duplicate.json()["clarifications"][0]["choices"][0]["resolved_id"] == (
        committed["takeaway_wallet_53"]["transaction_id"]
    )
