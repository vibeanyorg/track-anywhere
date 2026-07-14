from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.db.models.auth import CredentialRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


RAW_API_KEY = "ta_journal_contract"
EFFECTIVE_AT = "2026-07-14T12:30:00Z"


def _seed_authenticated_journal(engine) -> JournalScenario:
    scenario = JournalScenario.create()
    seed_journal_scenario(engine, scenario)
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=sha256(RAW_API_KEY.encode()).digest(),
                jti=uuid4(),
                actor_subject_id=scenario.actor_subject_id,
                actor_type="human",
                auth_kind="api_key",
                book_id=scenario.book_id,
                scopes=["ledger:write"],
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
                revoked_at=None,
                last_used_at=None,
            )
        )
    return scenario


def _session_dependencies(engine):
    factory = sessionmaker(engine, expire_on_commit=False)

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

    return get_session, lambda: SqlAlchemyUnitOfWork(factory)


def _journal_client(engine) -> TestClient:
    from track_anywhere.api.v2.journal import create_journal_router

    get_session, uow_factory = _session_dependencies(engine)
    app = FastAPI()
    app.include_router(
        create_journal_router(
            get_session=get_session,
            uow_factory=uow_factory,
            ledger_committer=LedgerCommitter(),
        ),
        prefix="/api/v2",
    )
    return TestClient(app)


def _post_payload(scenario: JournalScenario, *, amount: object = "12.34") -> dict:
    return {
        "command_id": str(scenario.command_id),
        "transaction_id": str(scenario.transaction_id),
        "expected_stream_version": 0,
        "kind": "standard",
        "effective_at": EFFECTIVE_AT,
        "description_ref": None,
        "external_references": [],
        "postings": [
            {
                "posting_id": str(scenario.debit_posting_id),
                "account_id": str(scenario.debit_account_id),
                "asset_code": "USD",
                "side": "debit",
                "amount": amount,
            },
            {
                "posting_id": str(scenario.credit_posting_id),
                "account_id": str(scenario.credit_account_id),
                "asset_code": "USD",
                "side": "credit",
                "amount": amount,
            },
        ],
    }


def _post_path(scenario: JournalScenario) -> str:
    return f"/api/v2/books/{scenario.book_id}/journal/transactions"


def _financial_headers(key: str = "journal-contract-key") -> dict[str, str]:
    return {"X-API-Key": RAW_API_KEY, "X-Idempotency-Key": key}


def test_post_requires_plain_decimal_strings_and_returns_book_position(
    pg_engine,
) -> None:
    scenario = _seed_authenticated_journal(pg_engine)
    client = _journal_client(pg_engine)

    for invalid_amount in (12.34, 12, "1e3", "-1", " 1"):
        response = client.post(
            _post_path(scenario),
            headers=_financial_headers(f"invalid:{invalid_amount!r}"),
            json=_post_payload(scenario, amount=invalid_amount),
        )
        assert response.status_code == 422

    posted = client.post(
        _post_path(scenario),
        headers=_financial_headers(),
        json=_post_payload(scenario),
    )

    assert posted.status_code == 201
    assert posted.json() == {
        "transaction_id": str(scenario.transaction_id),
        "as_of_book_position": 1,
    }
    assert posted.headers["Idempotency-Replayed"] == "false"


def test_openapi_lists_every_implemented_financial_command_and_no_query_routes(
    pg_engine,
) -> None:
    from track_anywhere.api.v2.router import create_v2_router

    get_session, _ = _session_dependencies(pg_engine)
    app = FastAPI()
    app.include_router(
        create_v2_router(
            engine=pg_engine,
            expected_runtime_role=pg_engine.url.username,
            get_session=get_session,
        )
    )
    paths = set(app.openapi()["paths"])

    assert {
        "/api/v2/books/{book_id}/journal/transactions",
        "/api/v2/books/{book_id}/journal/transactions/{transaction_id}/reverse",
        "/api/v2/books/{book_id}/journal/transactions/{transaction_id}/correct",
        "/api/v2/books/{book_id}/journal/transactions/{transaction_id}/external-references/correct",
        "/api/v2/books/{book_id}/journal/transactions/{transaction_id}/reporting-lines/assign",
        "/api/v2/books/{book_id}/journal/transactions/{transaction_id}/reporting-lines/clear",
        "/api/v2/books/{book_id}/journal/fx",
        "/api/v2/books/{book_id}/investments/lots/acquire",
        "/api/v2/books/{book_id}/investments/lots/dispose",
    } <= paths
    assert "/api/v2/books/{book_id}/balances" not in paths
