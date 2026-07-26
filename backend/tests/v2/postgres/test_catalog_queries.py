from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.tests.v2.fixtures.synchronous import (
    JournalScenario,
    seed_journal_scenario,
)


def test_catalog_queries_list_accessible_books_and_zero_balance_accounts(
    pg_engine,
) -> None:
    try:
        from track_anywhere.queries.catalogs import (
            get_account,
            list_account_page,
            list_accessible_books,
            list_accounts,
            list_assets,
            list_categories,
        )
    except (ImportError, ModuleNotFoundError) as error:
        pytest.fail(f"V2 catalog query service is missing: {error}")

    scenario = JournalScenario.create()
    seed_journal_scenario(pg_engine, scenario)
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

    with Session(pg_engine) as session:
        books = list_accessible_books(session, user_id=scenario.actor_subject_id)
        assets = list_assets(session, scenario.book_id)
        accounts = list_accounts(
            session,
            scenario.book_id,
            account_type="asset",
            status="active",
        )
        first_page = list_account_page(
            session,
            scenario.book_id,
            account_type="asset",
            status="active",
            limit=1,
            offset=0,
        )
        second_page = list_account_page(
            session,
            scenario.book_id,
            account_type="asset",
            status="active",
            limit=1,
            offset=1,
        )
        account = get_account(session, scenario.book_id, scenario.debit_account_id)
        categories = list_categories(session, scenario.book_id)

    assert [book.book_id for book in books] == [scenario.book_id]
    assert [asset.asset_code for asset in assets] == ["USD"]
    assert {item.account_id for item in accounts} == {
        scenario.debit_account_id,
        scenario.credit_account_id,
    }
    assert all(item.balance.natural_units == 0 for item in accounts)
    assert first_page.total == 2
    assert second_page.total == 2
    assert first_page.items + second_page.items == accounts
    assert account.current_name == "Debit"
    assert account.balance.raw_accounting_units == 0
    assert categories == ()
