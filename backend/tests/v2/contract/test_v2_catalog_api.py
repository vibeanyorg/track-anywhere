from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.api.v2.schemas import call_application
from track_anywhere.application.catalogs.close_account import (
    AccountBalanceProjectionMismatch,
)
from track_anywhere.infrastructure.db.models.auth import CredentialRecord, UserRecord
from track_anywhere.infrastructure.db.models.catalog import AccountRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


RAW_API_KEY = "ta_catalog_contract"
ACTOR_SUBJECT = "human:catalog-api"


def _seed_actor(engine) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        session.add(
            UserRecord(
                user_id=ACTOR_SUBJECT,
                subject_type="human",
                current_display_name="Catalog API",
                status="active",
            )
        )
        session.flush()
        session.add(
            CredentialRecord(
                credential_id=uuid4(),
                token_hash=sha256(RAW_API_KEY.encode()).digest(),
                jti=uuid4(),
                actor_subject_id=ACTOR_SUBJECT,
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


def _catalog_client(engine) -> TestClient:
    from track_anywhere.api.v2.catalogs import create_catalog_router

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

    app = FastAPI()
    app.include_router(
        create_catalog_router(
            get_session=get_session,
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        ),
        prefix="/api/v2",
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-API-Key": RAW_API_KEY}


def test_catalog_routes_delegate_to_handlers_and_return_book_positions(
    pg_engine,
) -> None:
    _seed_actor(pg_engine)
    client = _catalog_client(pg_engine)
    book_id = uuid4()
    account_id = uuid4()
    category_id = uuid4()
    category_version_id = uuid4()

    created_book = client.post(
        "/api/v2/books",
        headers=_headers(),
        json={
            "book_id": str(book_id),
            "current_name": "Household",
            "base_asset_code": None,
        },
    )
    assert created_book.status_code == 201
    assert created_book.json() == {
        "book_id": str(book_id),
        "as_of_book_position": 0,
    }

    created_asset = client.post(
        f"/api/v2/books/{book_id}/assets",
        headers=_headers(),
        json={
            "asset_code": "USD",
            "kind": "fiat",
            "ledger_scale": 2,
            "input_scale": 2,
            "display_scale": 2,
            "current_name": "US Dollar",
        },
    )
    assert created_asset.status_code == 201
    assert created_asset.json()["as_of_book_position"] == 0

    created_account = client.post(
        f"/api/v2/books/{book_id}/accounts",
        headers=_headers(),
        json={
            "account_id": str(account_id),
            "asset_code": "USD",
            "account_type": "liability",
            "account_subtype": "credit_card",
            "current_name": "Card",
            "system_role": None,
        },
    )
    assert created_account.status_code == 201
    assert created_account.json()["as_of_book_position"] == 0

    created_category = client.post(
        f"/api/v2/books/{book_id}/categories",
        headers=_headers(),
        json={
            "category_id": str(category_id),
            "category_version_id": str(category_version_id),
            "name": "Food",
            "parent_category_id": None,
            "change_reason_code": "created",
        },
    )
    assert created_category.status_code == 201
    assert created_category.json()["as_of_book_position"] == 0

    closed = client.post(
        f"/api/v2/books/{book_id}/accounts/{account_id}/close",
        headers=_headers(),
    )
    assert closed.status_code == 200
    assert closed.json() == {
        "account_id": str(account_id),
        "as_of_book_position": 0,
        "status": "closed",
    }
    reopened = client.post(
        f"/api/v2/books/{book_id}/accounts/{account_id}/reopen",
        headers=_headers(),
    )
    assert reopened.status_code == 200
    assert reopened.json() == {
        "account_id": str(account_id),
        "as_of_book_position": 0,
        "status": "active",
    }

    with Session(pg_engine) as session:
        account = session.get(AccountRecord, (book_id, account_id))
        assert account is not None
        assert account.status == "active"
        assert account.account_type == "liability"
        assert account.account_subtype == "credit_card"


def test_catalog_account_request_fails_closed_for_type_and_subtype(pg_engine) -> None:
    _seed_actor(pg_engine)
    client = _catalog_client(pg_engine)
    book_id = uuid4()

    created_book = client.post(
        "/api/v2/books",
        headers=_headers(),
        json={
            "book_id": str(book_id),
            "current_name": "Household",
            "base_asset_code": None,
        },
    )
    assert created_book.status_code == 201

    for account_type, account_subtype in (
        ("receivable", None),
        ("ASSET", None),
        ("liability", "Credit_Card"),
        ("liability", "credit-card"),
        ("asset", "credit_card"),
    ):
        response = client.post(
            f"/api/v2/books/{book_id}/accounts",
            headers=_headers(),
            json={
                "account_id": str(uuid4()),
                "asset_code": "USD",
                "account_type": account_type,
                "account_subtype": account_subtype,
                "current_name": "Invalid",
                "system_role": None,
            },
        )
        assert response.status_code == 422


def test_catalog_write_requires_an_authenticated_request_actor(pg_engine) -> None:
    _seed_actor(pg_engine)
    client = _catalog_client(pg_engine)

    response = client.post(
        "/api/v2/books",
        json={
            "book_id": str(uuid4()),
            "current_name": "Hidden",
            "base_asset_code": None,
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication is required"}


def test_account_balance_projection_mismatch_is_an_api_conflict() -> None:
    app = FastAPI()

    @app.post("/close")
    def close_with_corrupt_projection() -> object:
        def fail() -> object:
            raise AccountBalanceProjectionMismatch("projection is corrupt")

        return call_application(fail)

    response = TestClient(app, raise_server_exceptions=False).post("/close")

    assert response.status_code == 409
    assert response.json() == {"detail": "command conflict"}
