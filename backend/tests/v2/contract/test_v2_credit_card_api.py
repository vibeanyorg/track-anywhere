from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.tests.v2.fixtures.synchronous import JournalScenario, seed_journal_scenario
from track_anywhere.api.v2.credit_cards import create_credit_card_router
from track_anywhere.api.v2.journal import create_journal_router
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.db.models.auth import CredentialRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


RAW_API_KEY = "ta_credit_card_contract"
EFFECTIVE_AT = "2026-07-15T12:00:00Z"


def _client(
    engine,
    *,
    include_journal: bool = False,
) -> tuple[TestClient, JournalScenario, object]:
    base = JournalScenario.create()
    seed_journal_scenario(engine, base)
    scenario = replace(
        base,
        debit_account_id=uuid4(),
        credit_account_id=uuid4(),
    )
    source_id = uuid4()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into accounts (book_id, account_id, asset_code, "
                "account_type, account_subtype, current_name, status) values "
                "(:book_id, :expense_id, 'USD', 'expense', null, "
                "'Card expense', 'active'), "
                "(:book_id, :card_id, 'USD', 'liability', 'credit_card', "
                "'Credit card', 'active'), "
                "(:book_id, :source_id, 'USD', 'asset', null, "
                "'Checking', 'active')"
            ),
            {
                "book_id": scenario.book_id,
                "expense_id": scenario.debit_account_id,
                "card_id": scenario.credit_account_id,
                "source_id": source_id,
            },
        )
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
    factory = sessionmaker(engine, expire_on_commit=False)

    def get_session():
        with factory() as session:
            with session.begin():
                yield session

    app = FastAPI()
    app.include_router(
        create_credit_card_router(
            get_session=get_session,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            ledger_committer=LedgerCommitter(),
        ),
        prefix="/api/v2",
    )
    if include_journal:
        app.include_router(
            create_journal_router(
                get_session=get_session,
                uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
                ledger_committer=LedgerCommitter(),
            ),
            prefix="/api/v2",
        )
    return TestClient(app), scenario, source_id


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": RAW_API_KEY, "X-Idempotency-Key": key}


def _base(scenario: JournalScenario) -> dict[str, object]:
    return {
        "command_id": str(uuid4()),
        "transaction_id": str(uuid4()),
        "expected_stream_version": 0,
        "card_account_id": str(scenario.credit_account_id),
        "asset_code": "USD",
        "amount": "10.00",
        "effective_at": EFFECTIVE_AT,
        "description_ref": None,
        "external_references": [],
    }


def test_semantic_routes_are_strict_idempotent_and_hide_posting_sides(
    pg_engine,
) -> None:
    client, scenario, source_id = _client(pg_engine)
    path = f"/api/v2/books/{scenario.book_id}/credit-cards"
    charge = _base(scenario) | {"expense_account_id": str(scenario.debit_account_id)}
    missing_key = client.post(
        f"{path}/charges",
        headers={"X-API-Key": RAW_API_KEY},
        json=charge,
    )
    assert missing_key.status_code == 400
    extra = client.post(
        f"{path}/charges",
        headers=_headers("charge-extra"),
        json=charge | {"side": "credit"},
    )
    assert extra.status_code == 422

    charged = client.post(f"{path}/charges", headers=_headers("charge"), json=charge)
    assert charged.status_code == 201
    assert charged.json()["intent"] == "charge"
    replayed = client.post(f"{path}/charges", headers=_headers("charge"), json=charge)
    assert replayed.status_code == 201
    assert replayed.headers["Idempotency-Replayed"] == "true"

    requests = (
        ("payments", _base(scenario) | {"source_account_id": str(source_id)}),
        (
            "refunds",
            _base(scenario)
            | {
                "original_transaction_id": charge["transaction_id"],
                "amount": "5.00",
            },
        ),
        (
            "fees",
            _base(scenario) | {"expense_account_id": str(scenario.debit_account_id)},
        ),
    )
    for route, payload in requests:
        response = client.post(
            f"{path}/{route}",
            headers=_headers(route),
            json=payload,
        )
        assert response.status_code == 201, response.text

    schema = client.get("/openapi.json").json()
    assert {
        "/api/v2/books/{book_id}/credit-cards/charges",
        "/api/v2/books/{book_id}/credit-cards/payments",
        "/api/v2/books/{book_id}/credit-cards/refunds",
        "/api/v2/books/{book_id}/credit-cards/fees",
    } <= set(schema["paths"])
    serialized = str(schema["components"]["schemas"])
    assert "posting_id" not in serialized
    assert "PostingSide" not in serialized


def test_general_correction_of_a_typed_credit_card_transaction_is_a_conflict(
    pg_engine,
) -> None:
    client, scenario, _ = _client(pg_engine, include_journal=True)
    card_path = f"/api/v2/books/{scenario.book_id}/credit-cards"
    charge = _base(scenario) | {"expense_account_id": str(scenario.debit_account_id)}
    charged = client.post(
        f"{card_path}/charges",
        headers=_headers("charge-before-correct"),
        json=charge,
    )
    assert charged.status_code == 201

    correction = {
        "command_id": str(uuid4()),
        "reversal_transaction_id": str(uuid4()),
        "expected_reversal_stream_version": 0,
        "reason_code": "user_correction",
        "reversal_effective_at": "2026-07-15T12:01:00Z",
        "reversal_description_ref": None,
        "replacement": {
            "transaction_id": str(uuid4()),
            "expected_stream_version": 0,
            "kind": "standard",
            "effective_at": "2026-07-15T12:02:00Z",
            "description_ref": None,
            "external_references": [],
            "postings": [
                {
                    "posting_id": str(uuid4()),
                    "account_id": str(scenario.debit_account_id),
                    "asset_code": "USD",
                    "side": "debit",
                    "amount": "10.00",
                },
                {
                    "posting_id": str(uuid4()),
                    "account_id": str(scenario.credit_account_id),
                    "asset_code": "USD",
                    "side": "credit",
                    "amount": "10.00",
                },
            ],
        },
    }
    response = client.post(
        f"/api/v2/books/{scenario.book_id}/journal/transactions/"
        f"{charge['transaction_id']}/correct",
        headers=_headers("correct-typed-card"),
        json=correction,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "command conflict"}


def test_excess_refund_is_an_api_state_conflict(pg_engine) -> None:
    client, scenario, _ = _client(pg_engine)
    path = f"/api/v2/books/{scenario.book_id}/credit-cards"
    charge = _base(scenario) | {"expense_account_id": str(scenario.debit_account_id)}
    charged = client.post(
        f"{path}/charges",
        headers=_headers("charge-before-excess-refund"),
        json=charge,
    )
    assert charged.status_code == 201

    refund = _base(scenario) | {
        "original_transaction_id": charge["transaction_id"],
        "amount": "10.01",
    }
    response = client.post(
        f"{path}/refunds",
        headers=_headers("excess-refund"),
        json=refund,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "command conflict"}


def test_refund_of_reversed_charge_is_an_api_state_conflict(pg_engine) -> None:
    client, scenario, _ = _client(pg_engine, include_journal=True)
    path = f"/api/v2/books/{scenario.book_id}/credit-cards"
    charge = _base(scenario) | {"expense_account_id": str(scenario.debit_account_id)}
    charged = client.post(
        f"{path}/charges",
        headers=_headers("charge-before-reversed-source-refund"),
        json=charge,
    )
    assert charged.status_code == 201
    reversed_charge = client.post(
        f"/api/v2/books/{scenario.book_id}/journal/transactions/"
        f"{charge['transaction_id']}/reverse",
        headers=_headers("reverse-charge-before-refund"),
        json={
            "command_id": str(uuid4()),
            "reversal_transaction_id": str(uuid4()),
            "expected_stream_version": 0,
            "reason_code": "user_correction",
            "effective_at": "2026-07-15T12:01:00Z",
            "description_ref": None,
        },
    )
    assert reversed_charge.status_code == 201, reversed_charge.text

    refund = _base(scenario) | {
        "original_transaction_id": charge["transaction_id"],
        "amount": "1.00",
        "effective_at": "2026-07-15T12:02:00Z",
    }
    response = client.post(
        f"{path}/refunds",
        headers=_headers("refund-reversed-charge"),
        json=refund,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "command conflict"}


def test_unknown_refund_source_remains_an_api_input_error(pg_engine) -> None:
    client, scenario, _ = _client(pg_engine)
    path = f"/api/v2/books/{scenario.book_id}/credit-cards/refunds"
    refund = _base(scenario) | {
        "original_transaction_id": str(uuid4()),
        "amount": "1.00",
    }

    response = client.post(
        path,
        headers=_headers("refund-unknown-charge"),
        json=refund,
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "refund source must be an existing typed credit-card charge"
    }
