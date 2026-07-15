from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.db.models.auth import CredentialRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


RAW_API_KEY = "ta_journal_contract"
EFFECTIVE_AT = "2026-07-14T12:30:00Z"


def _seed_authenticated_journal(
    engine,
    *,
    credit_account_type: str = "asset",
    credit_account_subtype: str | None = None,
) -> JournalScenario:
    scenario = JournalScenario.create()
    seed_journal_scenario(
        engine,
        scenario,
        credit_account_type=credit_account_type,
        credit_account_subtype=credit_account_subtype,
    )
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


def test_general_journal_api_rejects_credit_card_postings(pg_engine) -> None:
    scenario = _seed_authenticated_journal(
        pg_engine,
        credit_account_type="liability",
        credit_account_subtype="credit_card",
    )
    client = _journal_client(pg_engine)

    response = client.post(
        _post_path(scenario),
        headers=_financial_headers("generic-card-forbidden"),
        json=_post_payload(scenario),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "command conflict"}


def test_general_correction_api_rejects_a_credit_card_replacement(
    pg_engine,
) -> None:
    scenario = _seed_authenticated_journal(pg_engine)
    client = _journal_client(pg_engine)
    posted = client.post(
        _post_path(scenario),
        headers=_financial_headers("generic-source"),
        json=_post_payload(scenario),
    )
    assert posted.status_code == 201
    card_account_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts ("
                "book_id, account_id, asset_code, account_type, account_subtype, "
                "current_name, status) values ("
                ":book_id, :account_id, 'USD', 'liability', 'credit_card', "
                "'Card', 'active')"
            ),
            {"book_id": scenario.book_id, "account_id": card_account_id},
        )
    correction = {
        "command_id": str(uuid4()),
        "reversal_transaction_id": str(uuid4()),
        "expected_reversal_stream_version": 0,
        "reason_code": "user_correction",
        "reversal_effective_at": "2026-07-14T12:31:00Z",
        "reversal_description_ref": None,
        "replacement": {
            "transaction_id": str(uuid4()),
            "expected_stream_version": 0,
            "kind": "standard",
            "effective_at": "2026-07-14T12:32:00Z",
            "description_ref": None,
            "external_references": [],
            "postings": [
                {
                    "posting_id": str(uuid4()),
                    "account_id": str(scenario.debit_account_id),
                    "asset_code": "USD",
                    "side": "debit",
                    "amount": "12.34",
                },
                {
                    "posting_id": str(uuid4()),
                    "account_id": str(card_account_id),
                    "asset_code": "USD",
                    "side": "credit",
                    "amount": "12.34",
                },
            ],
        },
    }

    response = client.post(
        f"{_post_path(scenario)}/{scenario.transaction_id}/correct",
        headers=_financial_headers("generic-card-replacement-forbidden"),
        json=correction,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "command conflict"}


def test_openapi_lists_every_implemented_financial_command_and_query_routes(
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
    assert {
        "/api/v2/books/{book_id}/balances",
        "/api/v2/books/{book_id}/journal",
        "/api/v2/books/{book_id}/reporting-lines",
    } <= paths
