from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.api.v2.catalogs import create_catalog_router
from track_anywhere.api.v2.credit_cards import create_credit_card_router
from track_anywhere.application.ledger_committer import LedgerCommitter
from track_anywhere.infrastructure.db.models.auth import CredentialRecord, UserRecord
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.models.credit_cards import (
    CreditCardTransactionRecord,
)
from track_anywhere.infrastructure.db.models.projections import AccountBalanceRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from track_anywhere_cli.click_app import run


TOKEN = "ta_credit_card_cli_contract"
ACTOR = "human:credit-card-cli"
EFFECTIVE_AT = "2026-07-15T12:00:00Z"


def _client(engine) -> TestClient:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        session.add(
            UserRecord(
                user_id=ACTOR,
                subject_type="human",
                current_display_name="Credit Card CLI",
                status="active",
            )
        )
        session.flush()
        session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=sha256(TOKEN.encode()).digest(),
                jti=uuid4(),
                actor_subject_id=ACTOR,
                actor_type="human",
                auth_kind="api_key",
                book_id=None,
                scopes=["book:write", "ledger:write"],
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
        create_catalog_router(
            get_session=get_session,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        ),
        prefix="/api/v2",
    )
    app.include_router(
        create_credit_card_router(
            get_session=get_session,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            ledger_committer=LedgerCommitter(),
        ),
        prefix="/api/v2",
    )
    return TestClient(app)


def _requester(client: TestClient):
    def request(config, method, path, payload=None, key=None):
        assert config.token is None
        assert config.api_key == TOKEN
        headers = {"X-API-Key": config.api_key}
        if key is not None:
            headers["X-Idempotency-Key"] = key
        response = client.request(method, path, headers=headers, json=payload)
        return response.status_code, response.json()

    return request


def _run(requester, *args: str) -> None:
    assert run(["--insecure-automation", *args, "--json"], requester=requester) == 0


def test_cli_creates_credit_card_then_records_charge_and_payment(
    pg_engine,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRACK_ANYWHERE_API_KEY", TOKEN)
    requester = _requester(_client(pg_engine))
    book_id = uuid4()
    card_id = uuid4()
    expense_id = uuid4()
    source_id = uuid4()
    charge_id = uuid4()
    payment_id = uuid4()

    _run(requester, "book", "create", str(book_id), "--name", "CLI ledger")
    _run(
        requester,
        "asset",
        "create",
        str(book_id),
        "USD",
        "--kind",
        "fiat",
        "--ledger-scale",
        "2",
        "--input-scale",
        "2",
        "--display-scale",
        "2",
        "--name",
        "US Dollar",
    )
    for account_id, account_type, name, subtype in (
        (card_id, "liability", "Credit card", "credit_card"),
        (expense_id, "expense", "Card expense", None),
        (source_id, "asset", "Checking", None),
    ):
        arguments = [
            "account",
            "create",
            str(book_id),
            str(account_id),
            "--asset-code",
            "USD",
            "--type",
            account_type,
            "--name",
            name,
        ]
        if subtype is not None:
            arguments.extend(("--account-subtype", subtype))
        _run(requester, *arguments)

    _run(
        requester,
        "card",
        "charge",
        str(book_id),
        str(charge_id),
        "--command-id",
        str(uuid4()),
        "--card-account-id",
        str(card_id),
        "--expense-account-id",
        str(expense_id),
        "--asset-code",
        "USD",
        "--amount",
        "10.00",
        "--effective-at",
        EFFECTIVE_AT,
        "--idempotency-key",
        "credit-card-cli-charge",
    )
    _run(
        requester,
        "card",
        "payment",
        str(book_id),
        str(payment_id),
        "--command-id",
        str(uuid4()),
        "--card-account-id",
        str(card_id),
        "--source-account-id",
        str(source_id),
        "--asset-code",
        "USD",
        "--amount",
        "3.50",
        "--effective-at",
        EFFECTIVE_AT,
        "--idempotency-key",
        "credit-card-cli-payment",
    )
    capsys.readouterr()

    with Session(pg_engine) as session:
        card = session.get(AccountRecord, (book_id, card_id))
        assert card is not None
        assert (card.account_type, card.account_subtype) == (
            "liability",
            "credit_card",
        )
        transactions = session.scalars(
            select(CreditCardTransactionRecord)
            .where(CreditCardTransactionRecord.book_id == book_id)
            .order_by(CreditCardTransactionRecord.source_position)
        ).all()
        assert [transaction.intent for transaction in transactions] == [
            "charge",
            "payment",
        ]
        assert [transaction.units for transaction in transactions] == [1000, 350]
        balance = session.get(AccountBalanceRecord, (book_id, card_id, "USD"))
        assert balance is not None
        assert balance.balance_units == -650
