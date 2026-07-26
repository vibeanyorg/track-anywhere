from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
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


RAW_API_KEY = "ta_everyday_entry_contract"
OTHER_API_KEY = "ta_everyday_entry_other"
SOURCE_TEXT = "private OCR: lunch 12.34"
MERCHANT = "Private Lunch Merchant"


def _seed(pg_engine) -> tuple[JournalScenario, UUID]:
    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
    category_id = uuid4()
    category_version_id = uuid4()
    now = datetime.now(UTC)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "update book_members set scopes = "
                "'[\"ledger:read\",\"ledger:write\"]'::jsonb "
                "where book_id = :book_id and user_id = :user_id"
            ),
            {
                "book_id": scenario.book_id,
                "user_id": scenario.actor_subject_id,
            },
        )
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, system_role, "
                "current_name, status) values ("
                ":book_id, :account_id, 'USD', 'expense', 'expense_clearing', "
                "'Expense clearing', 'active')"
            ),
            {"book_id": scenario.book_id, "account_id": uuid4()},
        )
        connection.execute(
            text(
                "insert into categories ("
                "book_id, category_id, parent_category_id, current_name, "
                "current_version_id, status) values ("
                ":book_id, :category_id, null, 'Dining', null, 'active')"
            ),
            {"book_id": scenario.book_id, "category_id": category_id},
        )
        connection.execute(
            text(
                "insert into category_versions ("
                "book_id, category_id, category_version_id, parent_category_id, "
                "name, status, usage_kind, change_reason_code) values ("
                ":book_id, :category_id, :version_id, null, 'Dining', "
                "'active', 'expense', 'created')"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
            },
        )
        connection.execute(
            text(
                "update categories set current_version_id = :version_id "
                "where book_id = :book_id and category_id = :category_id"
            ),
            {
                "book_id": scenario.book_id,
                "category_id": category_id,
                "version_id": category_version_id,
            },
        )
        connection.execute(
            text(
                "insert into users (user_id, subject_type, current_display_name, status) "
                "values ('human:other-entry', 'human', 'Other', 'active')"
            )
        )
        connection.execute(
            text(
                "insert into book_members (book_id, user_id, role, status, scopes) "
                "values (:book_id, 'human:other-entry', 'editor', 'active', "
                "'[\"ledger:read\",\"ledger:write\"]'::jsonb)"
            ),
            {"book_id": scenario.book_id},
        )
    with Session(pg_engine) as session, session.begin():
        for raw_key, actor in (
            (RAW_API_KEY, scenario.actor_subject_id),
            (OTHER_API_KEY, "human:other-entry"),
        ):
            session.add(
                CredentialRecord(
                    credential_id=uuid4(),
                    token_hash=sha256(raw_key.encode()).digest(),
                    jti=uuid4(),
                    actor_subject_id=actor,
                    actor_type="human",
                    auth_kind="api_key",
                    book_id=scenario.book_id,
                    scopes=["ledger:read", "ledger:write"],
                    issued_at=now - timedelta(minutes=1),
                    expires_at=now + timedelta(hours=1),
                    revoked_at=None,
                    last_used_at=None,
                )
            )
    return scenario, category_id


def _client(pg_engine) -> TestClient:
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

    cipher = ProtectedContentCipher(
        ProtectedContentKeyring.from_mapping(
            active_key_ref="test-v1",
            keys={"test-v1": b"p" * 32},
        )
    )
    app = FastAPI()
    app.state.public_base_url = "http://testserver"
    app.include_router(
        create_entry_router(
            get_session=get_session,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            ledger_committer=LedgerCommitter(),
            protected_content_cipher=cipher,
            duplicate_key_provider=DuplicateDetectionKeyProvider(bytes(range(32))),
        ),
        prefix="/api/v2",
    )
    install_error_handlers(app)
    return TestClient(app)


def _prepare_payload(
    scenario: JournalScenario,
    category_id: UUID,
) -> dict[str, object]:
    return {
        "kind": "expense",
        "occurred_at": "2026-07-24T12:30:00Z",
        "amount": {
            "value": "12.34",
            "denomination": "asset_unit",
            "asset_code": "USD",
            "source_text": SOURCE_TEXT,
        },
        "source_account": {"account_id": str(scenario.credit_account_id)},
        "category": {"category_id": str(category_id)},
        "category_allocations": [],
        "narrative": {
            "merchant": MERCHANT,
            "channel": None,
            "note": None,
            "external_reference": None,
            "gross_amount": None,
            "discount_amount": None,
        },
    }


def test_entry_api_auth_schema_actor_scope_and_narrative_redaction(pg_engine) -> None:
    scenario, category_id = _seed(pg_engine)
    client = _client(pg_engine)
    path = f"/api/v2/books/{scenario.book_id}/entries/prepare"
    payload = _prepare_payload(scenario, category_id)

    unauthenticated = client.post(path, json=payload)
    assert unauthenticated.status_code == 401

    prepared_response = client.post(
        path,
        json=payload,
        headers={"X-API-Key": RAW_API_KEY},
    )
    assert prepared_response.status_code == 200
    assert SOURCE_TEXT not in prepared_response.text
    assert MERCHANT not in prepared_response.text
    prepared = prepared_response.json()

    request_id = uuid4()
    commit_path = f"/api/v2/books/{scenario.book_id}/entries/commit"
    commit_payload = {
        "intent_id": prepared["intent_id"],
        "commit_token": prepared["commit_token"],
        "request_id": str(request_id),
    }
    wrong_token = client.post(
        commit_path,
        json={**commit_payload, "commit_token": "x" * 32},
        headers={
            "X-API-Key": RAW_API_KEY,
            "X-Idempotency-Key": str(request_id),
        },
    )
    assert wrong_token.status_code == 404
    assert wrong_token.json()["detail"] == {
        "code": "entry_intent_not_found",
        "message": "entry intent was not found",
        "field": None,
        "retryable": False,
    }
    cross_actor = client.post(
        commit_path,
        json=commit_payload,
        headers={
            "X-API-Key": OTHER_API_KEY,
            "X-Idempotency-Key": str(request_id),
        },
    )
    assert cross_actor.status_code == 404
    assert cross_actor.json()["detail"]["code"] == "entry_intent_not_found"
    assert SOURCE_TEXT not in cross_actor.text

    mismatched = client.post(
        commit_path,
        json=commit_payload,
        headers={
            "X-API-Key": RAW_API_KEY,
            "X-Idempotency-Key": str(uuid4()),
        },
    )
    assert mismatched.status_code == 400

    committed_response = client.post(
        commit_path,
        json=commit_payload,
        headers={
            "X-API-Key": RAW_API_KEY,
            "X-Idempotency-Key": str(request_id),
        },
    )
    assert committed_response.status_code == 201
    committed = committed_response.json()
    receipt_path = (
        f"/api/v2/books/{scenario.book_id}/entries/{committed['transaction_id']}"
    )
    redacted = client.get(
        receipt_path,
        headers={"X-API-Key": RAW_API_KEY},
    )
    assert redacted.status_code == 200
    assert redacted.json()["narrative"] == {
        "status": "redacted",
        "merchant": None,
        "channel": None,
    }
    assert MERCHANT not in redacted.text

    disclosed = client.get(
        receipt_path,
        params={"include_narrative": "true"},
        headers={"X-API-Key": RAW_API_KEY},
    )
    assert disclosed.status_code == 200
    assert disclosed.json()["narrative"]["merchant"] == MERCHANT
    assert SOURCE_TEXT not in disclosed.text


def test_commit_body_rejects_business_fact_retransmission(pg_engine) -> None:
    scenario, _ = _seed(pg_engine)
    response = _client(pg_engine).post(
        f"/api/v2/books/{scenario.book_id}/entries/commit",
        json={
            "intent_id": str(uuid4()),
            "commit_token": "x" * 32,
            "request_id": str(uuid4()),
            "amount": "12.34",
        },
        headers={
            "X-API-Key": RAW_API_KEY,
            "X-Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert "12.34" not in response.text
