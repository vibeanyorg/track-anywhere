from __future__ import annotations

from dataclasses import FrozenInstanceError
from inspect import signature
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from track_anywhere.infrastructure.db.repositories import RowLock
from track_anywhere.infrastructure.db.repositories.catalogs import CatalogRepository


def _seed_catalog(pg_engine):
    book_a = uuid4()
    book_b = uuid4()
    account_id = uuid4()
    category_id = uuid4()
    version_a = uuid4()
    version_b = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(
            text("""
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
            """)
        )
        for book_id, name in ((book_a, "Book A"), (book_b, "Book B")):
            connection.execute(
                text("""
                insert into books (
                    book_id, current_name, base_asset_code, write_state
                ) values (:book_id, :name, 'USD', 'active')
                """),
                {"book_id": book_id, "name": name},
            )
            connection.execute(
                text("""
                insert into accounts (
                    book_id, account_id, asset_code, account_type,
                    current_name, status
                ) values (:book_id, :account_id, 'USD', 'asset', :name, 'active')
                """),
                {"book_id": book_id, "account_id": account_id, "name": name},
            )
            connection.execute(
                text("""
                insert into categories (
                    book_id, category_id, current_name, status
                ) values (:book_id, :category_id, :name, 'active')
                """),
                {"book_id": book_id, "category_id": category_id, "name": name},
            )
        for book_id, version_id, name in (
            (book_a, version_a, "A version"),
            (book_b, version_b, "B version"),
        ):
            connection.execute(
                text("""
                insert into category_versions (
                    book_id, category_id, category_version_id, name, status,
                    change_reason_code
                ) values (
                    :book_id, :category_id, :version_id, :name, 'active', 'created'
                )
                """),
                {
                    "book_id": book_id,
                    "category_id": category_id,
                    "version_id": version_id,
                    "name": name,
                },
            )
            connection.execute(
                text("""
                update categories set current_version_id = :version_id
                 where book_id = :book_id and category_id = :category_id
                """),
                {
                    "book_id": book_id,
                    "category_id": category_id,
                    "version_id": version_id,
                },
            )
    return book_a, book_b, account_id, category_id, version_a


def test_catalog_reads_are_book_scoped_immutable_snapshots(pg_engine) -> None:
    book_a, book_b, account_id, category_id, version_a = _seed_catalog(pg_engine)
    with Session(pg_engine) as session:
        repository = CatalogRepository(session)
        account_a = repository.get_account(book_a, account_id)
        account_b = repository.get_account(book_b, account_id)
        category = repository.get_category(book_a, category_id)
        version = repository.get_category_version(book_a, category_id, version_a)

    assert account_a.current_name == "Book A"
    assert account_b.current_name == "Book B"
    assert category.current_name == "Book A"
    assert version.name == "A version"
    with pytest.raises(FrozenInstanceError):
        account_a.current_name = "mutated"  # type: ignore[misc]
    assert list(signature(CatalogRepository.get_account).parameters)[:3] == [
        "self",
        "book_id",
        "account_id",
    ]
    assert not hasattr(repository, "get_account_global")


def test_catalog_share_lock_blocks_racing_account_close(pg_engine) -> None:
    book_id, _book_b, account_id, _category_id, _version = _seed_catalog(pg_engine)
    first = Session(pg_engine)
    second = Session(pg_engine)
    try:
        CatalogRepository(first).get_account(
            book_id,
            account_id,
            lock=RowLock.SHARE,
        )
        second.execute(text("set local lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError) as error_info:
            second.execute(
                text("""
                update accounts set status = 'closed'
                 where book_id = :book_id and account_id = :account_id
                """),
                {"book_id": book_id, "account_id": account_id},
            )
        assert getattr(error_info.value.orig, "sqlstate", "") == "55P03"
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()
    with pg_engine.begin() as connection:
        updated = connection.execute(
            text("""
            update accounts set status = 'closed'
             where book_id = :book_id and account_id = :account_id
            """),
            {"book_id": book_id, "account_id": account_id},
        ).rowcount
    assert updated == 1


def test_catalog_update_lock_blocks_racing_shared_reader(pg_engine) -> None:
    book_id, _book_b, account_id, _category_id, _version = _seed_catalog(pg_engine)
    first = Session(pg_engine)
    second = Session(pg_engine)
    try:
        CatalogRepository(first).get_account(
            book_id,
            account_id,
            lock=RowLock.UPDATE,
        )
        second.execute(text("set local lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError) as error_info:
            CatalogRepository(second).get_account(
                book_id,
                account_id,
                lock=RowLock.SHARE,
            )
        assert getattr(error_info.value.orig, "sqlstate", "") == "55P03"
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()
    with Session(pg_engine) as session:
        assert (
            CatalogRepository(session)
            .get_account(book_id, account_id, lock=RowLock.SHARE)
            .account_id
            == account_id
        )
