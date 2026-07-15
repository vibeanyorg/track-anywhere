from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from track_anywhere.domain.journal import AccountType, PostingSide
from track_anywhere.queries.balances import BalanceItem, BalanceSnapshot
from track_anywhere.queries.journal import (
    CreditCardRelation,
    JournalItem,
    JournalPage,
    JournalPosting,
)
from track_anywhere.queries.reporting import ReportingLine


BOOK_ID = UUID("11111111-1111-4111-8111-111111111111")
ACCOUNT_ID = UUID("22222222-2222-4222-8222-222222222222")
TRANSACTION_ID = UUID("33333333-3333-4333-8333-333333333333")
POSTING_ID = UUID("44444444-4444-4444-8444-444444444444")
REVERSAL_ID = UUID("55555555-5555-4555-8555-555555555555")
LINE_ID = UUID("66666666-6666-4666-8666-666666666666")
LINE_VERSION_ID = UUID("77777777-7777-4777-8777-777777777777")
DIMENSION_ID = UUID("88888888-8888-4888-8888-888888888888")
CATALOG_ID = UUID("99999999-9999-4999-8999-999999999999")
PRECISE_UNITS = 9_007_199_254_740_993_123_456
DUMMY_RUNTIME_URL = (
    "postgresql+psycopg://track_anywhere_runtime:secret@127.0.0.1:9/track_anywhere"
)


class _SessionSentinel:
    pass


SESSION = _SessionSentinel()


@pytest.fixture(autouse=True)
def _runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACK_ANYWHERE_DATABASE_URL", DUMMY_RUNTIME_URL)
    monkeypatch.setenv("TRACK_ANYWHERE_AUTH_COOKIE_SECURE", "0")


def _get_session() -> Iterator[Session]:
    yield cast(Session, SESSION)


def _client(authorize_book_read) -> TestClient:
    from track_anywhere.api.v2.queries import create_query_router

    app = FastAPI()
    app.include_router(
        create_query_router(
            _get_session,
            authorize_book_read=authorize_book_read,
        )
    )
    return TestClient(app)


def _default_auth_client() -> TestClient:
    from track_anywhere.api.v2.queries import create_query_router

    app = FastAPI()
    app.include_router(create_query_router(_get_session))
    return TestClient(app)


def test_journal_contract_forwards_cursor_and_as_of_and_stringifies_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    authorization_calls: list[tuple[object, Request, UUID]] = []
    query_calls: list[tuple[object, UUID, int, str | None, int | None]] = []

    def authorize(session: Session, request: Request, book_id: UUID) -> None:
        authorization_calls.append((session, request, book_id))

    def list_journal(
        session: Session,
        book_id: UUID,
        *,
        limit: int,
        cursor: str | None,
        as_of_book_position: int | None,
    ) -> JournalPage:
        query_calls.append((session, book_id, limit, cursor, as_of_book_position))
        return JournalPage(
            items=(
                JournalItem(
                    transaction_id=TRANSACTION_ID,
                    effective_at=datetime(
                        2026,
                        7,
                        14,
                        8,
                        9,
                        10,
                        123456,
                        tzinfo=UTC,
                    ),
                    book_position=40,
                    transaction_kind="expense",
                    postings=(
                        JournalPosting(
                            posting_id=POSTING_ID,
                            position=1,
                            account_id=ACCOUNT_ID,
                            asset_code="USD",
                            side="debit",
                            units=PRECISE_UNITS,
                        ),
                    ),
                    reversed_by_transaction_id=REVERSAL_ID,
                    reverses_transaction_id=None,
                    credit_card_relation=CreditCardRelation(
                        intent="refund",
                        card_account_id=ACCOUNT_ID,
                        counter_account_id=ACCOUNT_ID,
                        original_transaction_id=REVERSAL_ID,
                    ),
                ),
            ),
            next_cursor="cursor-next",
            as_of_book_position=41,
        )

    monkeypatch.setattr(query_api, "list_journal", list_journal)

    response = _client(authorize).get(
        f"/api/v2/books/{BOOK_ID}/journal",
        params={
            "limit": 2,
            "cursor": "cursor-current",
            "as_of_book_position": 41,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "transaction_id": str(TRANSACTION_ID),
                "effective_at": "2026-07-14T08:09:10.123456Z",
                "book_position": 40,
                "transaction_kind": "expense",
                "postings": [
                    {
                        "posting_id": str(POSTING_ID),
                        "position": 1,
                        "account_id": str(ACCOUNT_ID),
                        "asset_code": "USD",
                        "side": "debit",
                        "units": str(PRECISE_UNITS),
                    }
                ],
                "is_reversed": True,
                "reversed_by_transaction_id": str(REVERSAL_ID),
                "reverses_transaction_id": None,
                "credit_card_relation": {
                    "intent": "refund",
                    "card_account_id": str(ACCOUNT_ID),
                    "counter_account_id": str(ACCOUNT_ID),
                    "original_transaction_id": str(REVERSAL_ID),
                },
            }
        ],
        "next_cursor": "cursor-next",
        "as_of_book_position": 41,
    }
    assert query_calls == [
        (SESSION, BOOK_ID, 2, "cursor-current", 41),
    ]
    assert len(authorization_calls) == 1
    assert authorization_calls[0][0] is SESSION
    assert authorization_calls[0][2] == BOOK_ID


def test_balance_contract_names_raw_and_natural_liability_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    query_calls: list[tuple[object, UUID, int | None]] = []

    def get_book_balances(
        session: Session,
        book_id: UUID,
        *,
        as_of_book_position: int | None,
    ) -> BalanceSnapshot:
        query_calls.append((session, book_id, as_of_book_position))
        return BalanceSnapshot(
            items=(
                BalanceItem(
                    account_id=ACCOUNT_ID,
                    asset_code="JPY",
                    account_type=AccountType.LIABILITY,
                    account_subtype="credit_card",
                    account_status="closed",
                    raw_accounting_units=-PRECISE_UNITS,
                    natural_units=PRECISE_UNITS,
                    normal_side=PostingSide.CREDIT,
                    balance_semantics="natural_liability_balance",
                    outstanding_units=PRECISE_UNITS,
                    overpayment_units=0,
                ),
            ),
            as_of_book_position=19,
            projection_matches_reference=True,
        )

    monkeypatch.setattr(query_api, "get_book_balances", get_book_balances)

    response = _client(lambda *_args: None).get(
        f"/api/v2/books/{BOOK_ID}/balances",
        params={"as_of_book_position": 19},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "account_id": str(ACCOUNT_ID),
                "asset_code": "JPY",
                "account_type": "liability",
                "account_subtype": "credit_card",
                "account_status": "closed",
                "raw_accounting_units": str(-PRECISE_UNITS),
                "natural_units": str(PRECISE_UNITS),
                "normal_side": "credit",
                "balance_semantics": "natural_liability_balance",
                "outstanding_units": str(PRECISE_UNITS),
                "overpayment_units": "0",
            }
        ],
        "as_of_book_position": 19,
        "projection_matches_reference": True,
    }
    assert query_calls == [(SESSION, BOOK_ID, 19)]


def test_catalog_read_contracts_include_zero_balances_and_current_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    zero_balance = BalanceItem(
        account_id=ACCOUNT_ID,
        asset_code="CNY",
        account_type=AccountType.LIABILITY,
        account_subtype="credit_card",
        account_status="active",
        raw_accounting_units=0,
        natural_units=0,
        normal_side=PostingSide.CREDIT,
        balance_semantics="natural_liability_balance",
        outstanding_units=0,
        overpayment_units=0,
    )
    book = SimpleNamespace(
        book_id=BOOK_ID,
        current_name="Personal",
        base_asset_code="CNY",
        write_state="active",
    )
    asset = SimpleNamespace(
        asset_code="CNY",
        kind="fiat",
        ledger_scale=2,
        input_scale=2,
        display_scale=2,
        current_name="人民币",
        status="active",
    )
    account = SimpleNamespace(
        account_id=ACCOUNT_ID,
        asset_code="CNY",
        account_type=AccountType.LIABILITY,
        account_subtype="credit_card",
        system_role=None,
        current_name="交通银行信用卡",
        status="active",
        balance=zero_balance,
    )
    category = SimpleNamespace(
        category_id=DIMENSION_ID,
        parent_category_id=None,
        current_version_id=LINE_VERSION_ID,
        current_name="餐饮",
        status="active",
    )
    monkeypatch.setattr(
        query_api,
        "_request_identity",
        lambda *_args: SimpleNamespace(
            user_id="human:reader",
            book_id=None,
            scopes=("ledger:read",),
        ),
    )
    monkeypatch.setattr(
        query_api,
        "list_accessible_books",
        lambda *_args, **_kwargs: (book,),
        raising=False,
    )
    monkeypatch.setattr(
        query_api,
        "list_assets",
        lambda *_args, **_kwargs: (asset,),
        raising=False,
    )
    monkeypatch.setattr(
        query_api,
        "list_accounts",
        lambda *_args, **_kwargs: (account,),
        raising=False,
    )
    monkeypatch.setattr(
        query_api,
        "get_account",
        lambda *_args, **_kwargs: account,
        raising=False,
    )
    monkeypatch.setattr(
        query_api,
        "list_categories",
        lambda *_args, **_kwargs: (category,),
        raising=False,
    )
    client = _client(lambda *_args: None)

    books = client.get("/api/v2/books")
    assets = client.get(f"/api/v2/books/{BOOK_ID}/assets")
    accounts = client.get(
        f"/api/v2/books/{BOOK_ID}/accounts",
        params={
            "account_type": "liability",
            "account_subtype": "credit_card",
            "status": "active",
            "asset_code": "CNY",
            "name": "交通",
        },
    )
    shown = client.get(f"/api/v2/books/{BOOK_ID}/accounts/{ACCOUNT_ID}")
    balance = client.get(
        f"/api/v2/books/{BOOK_ID}/accounts/{ACCOUNT_ID}/balance"
    )
    categories = client.get(f"/api/v2/books/{BOOK_ID}/categories")

    assert books.status_code == 200
    assert books.json()["items"] == [
        {
            "book_id": str(BOOK_ID),
            "current_name": "Personal",
            "base_asset_code": "CNY",
            "write_state": "active",
        }
    ]
    assert assets.status_code == 200
    assert assets.json()["items"][0]["current_name"] == "人民币"
    assert accounts.status_code == 200
    assert accounts.json()["items"] == [
        {
            "account_id": str(ACCOUNT_ID),
            "asset_code": "CNY",
            "account_type": "liability",
            "account_subtype": "credit_card",
            "system_role": None,
            "current_name": "交通银行信用卡",
            "status": "active",
            "balance": {
                "account_id": str(ACCOUNT_ID),
                "asset_code": "CNY",
                "account_type": "liability",
                "account_subtype": "credit_card",
                "account_status": "active",
                "raw_accounting_units": "0",
                "natural_units": "0",
                "normal_side": "credit",
                "balance_semantics": "natural_liability_balance",
                "outstanding_units": "0",
                "overpayment_units": "0",
            },
        }
    ]
    assert shown.json() == accounts.json()["items"][0]
    assert balance.json() == accounts.json()["items"][0]["balance"]
    assert categories.status_code == 200
    assert categories.json()["items"][0]["current_name"] == "餐饮"


def test_transaction_show_contract_returns_one_journal_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    item = JournalItem(
        transaction_id=TRANSACTION_ID,
        effective_at=datetime(2026, 7, 15, tzinfo=UTC),
        book_position=12,
        transaction_kind="standard",
        postings=(),
        reversed_by_transaction_id=None,
        reverses_transaction_id=None,
    )
    monkeypatch.setattr(
        query_api,
        "get_journal_transaction",
        lambda *_args, **_kwargs: item,
        raising=False,
    )

    response = _client(lambda *_args: None).get(
        f"/api/v2/books/{BOOK_ID}/journal/transactions/{TRANSACTION_ID}",
        params={"as_of_book_position": 12},
    )

    assert response.status_code == 200
    assert response.json() == {
        "transaction_id": str(TRANSACTION_ID),
        "effective_at": "2026-07-15T00:00:00.000000Z",
        "book_position": 12,
        "transaction_kind": "standard",
        "postings": [],
        "is_reversed": False,
        "reversed_by_transaction_id": None,
        "reverses_transaction_id": None,
        "credit_card_relation": None,
    }


def test_reporting_contract_requires_as_of_and_stringifies_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    query_calls: list[tuple[object, UUID, int]] = []

    def list_current_reporting_lines(
        session: Session,
        book_id: UUID,
        *,
        as_of_book_position: int,
    ) -> tuple[ReportingLine, ...]:
        query_calls.append((session, book_id, as_of_book_position))
        return (
            ReportingLine(
                transaction_id=TRANSACTION_ID,
                classification_revision=3,
                line_id=LINE_ID,
                line_version_id=LINE_VERSION_ID,
                catalog_id=CATALOG_ID,
                line_position=1,
                asset_code="CNY",
                units=PRECISE_UNITS,
                line_kind="category",
                dimension="category",
                dimension_id=DIMENSION_ID,
            ),
        )

    monkeypatch.setattr(
        query_api,
        "list_current_reporting_lines",
        list_current_reporting_lines,
    )
    client = _client(lambda *_args: None)

    missing = client.get(f"/api/v2/books/{BOOK_ID}/reporting-lines")
    response = client.get(
        f"/api/v2/books/{BOOK_ID}/reporting-lines",
        params={"as_of_book_position": 37},
    )

    assert missing.status_code == 422
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "transaction_id": str(TRANSACTION_ID),
                "classification_revision": 3,
                "line_id": str(LINE_ID),
                "line_version_id": str(LINE_VERSION_ID),
                "catalog_id": str(CATALOG_ID),
                "line_position": 1,
                "asset_code": "CNY",
                "units": str(PRECISE_UNITS),
                "line_kind": "category",
                "dimension": "category",
                "dimension_id": str(DIMENSION_ID),
            }
        ],
        "as_of_book_position": 37,
    }
    assert query_calls == [(SESSION, BOOK_ID, 37)]


def test_book_authorization_denial_prevents_query_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    def deny(_session: Session, _request: Request, _book_id: UUID) -> None:
        raise HTTPException(status_code=403, detail="Book read access is denied")

    def unexpected_query(*_args, **_kwargs):
        raise AssertionError("query must not execute after authorization fails")

    monkeypatch.setattr(query_api, "get_book_balances", unexpected_query)

    response = _client(deny).get(f"/api/v2/books/{BOOK_ID}/balances")

    assert response.status_code == 403
    assert response.json() == {"detail": "Book read access is denied"}


def test_query_errors_have_stable_http_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api
    from track_anywhere.queries.journal import InvalidJournalCursor

    client = _client(lambda *_args: None)

    def invalid_cursor(*_args, **_kwargs):
        raise InvalidJournalCursor("internal cursor detail")

    monkeypatch.setattr(query_api, "list_journal", invalid_cursor)
    invalid = client.get(
        f"/api/v2/books/{BOOK_ID}/journal",
        params={"cursor": "bad"},
    )

    def missing_book(*_args, **_kwargs):
        raise LookupError("database detail")

    monkeypatch.setattr(query_api, "get_book_balances", missing_book)
    missing = client.get(f"/api/v2/books/{BOOK_ID}/balances")

    monkeypatch.setattr(query_api, "list_current_reporting_lines", missing_book)
    missing_reporting = client.get(
        f"/api/v2/books/{BOOK_ID}/reporting-lines",
        params={"as_of_book_position": 1},
    )

    def invalid_as_of(*_args, **_kwargs):
        raise ValueError("as_of_book_position is outside the Book head")

    monkeypatch.setattr(query_api, "list_current_reporting_lines", invalid_as_of)
    invalid_reporting = client.get(
        f"/api/v2/books/{BOOK_ID}/reporting-lines",
        params={"as_of_book_position": 99},
    )

    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "journal cursor is invalid"}
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Book not found"}
    assert missing_reporting.status_code == 404
    assert missing_reporting.json() == {"detail": "Book not found"}
    assert invalid_reporting.status_code == 400
    assert invalid_reporting.json() == {
        "detail": "as_of_book_position is outside the Book head"
    }


def test_default_authorizer_requires_credential_and_active_book_membership_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    class OAuthService:
        def __init__(self, session: Session) -> None:
            assert session is SESSION

        def token_status(self, raw_token: str) -> dict[str, object]:
            assert raw_token == "ta_api_key"
            return {
                "actor_subject_id": "human:reader",
                "book_id": str(BOOK_ID),
                "scopes": ["ledger:read"],
            }

    class Repository:
        def __init__(self, session: Session) -> None:
            assert session is SESSION

        def get_membership(self, book_id: UUID, user_id: str):
            assert (book_id, user_id) == (BOOK_ID, "human:reader")
            return SimpleNamespace(
                status="active",
                revoked_at=None,
                scopes=("ledger:read",),
            )

    monkeypatch.setattr(query_api, "PersistentOAuthService", OAuthService)
    monkeypatch.setattr(query_api, "AuthRepository", Repository)
    monkeypatch.setattr(
        query_api,
        "get_book_balances",
        lambda *_args, **_kwargs: BalanceSnapshot(
            items=(),
            as_of_book_position=0,
            projection_matches_reference=True,
        ),
    )

    response = _default_auth_client().get(
        f"/api/v2/books/{BOOK_ID}/balances",
        headers={"Authorization": "Bearer ta_api_key"},
    )

    assert response.status_code == 200


def test_default_authorizer_rejects_cross_book_credential_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from track_anywhere.api.v2 import queries as query_api

    other_book_id = UUID("99999999-9999-4999-8999-999999999999")

    class OAuthService:
        def __init__(self, _session: Session) -> None:
            pass

        def token_status(self, _raw_token: str) -> dict[str, object]:
            return {
                "actor_subject_id": "human:reader",
                "book_id": str(other_book_id),
                "scopes": ["ledger:read"],
            }

    class UnexpectedRepository:
        def __init__(self, _session: Session) -> None:
            raise AssertionError("cross-Book credential must be rejected first")

    def unexpected_query(*_args, **_kwargs):
        raise AssertionError("unauthorized query must not execute")

    monkeypatch.setattr(query_api, "PersistentOAuthService", OAuthService)
    monkeypatch.setattr(query_api, "AuthRepository", UnexpectedRepository)
    monkeypatch.setattr(query_api, "get_book_balances", unexpected_query)

    response = _default_auth_client().get(
        f"/api/v2/books/{BOOK_ID}/balances",
        headers={"Authorization": "Bearer ta_api_key"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Book read access is denied"}
