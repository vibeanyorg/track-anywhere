from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.application.catalogs.close_account import (
    AccountAlreadyClosed,
    CloseAccount,
    close_account,
)
from track_anywhere.application.catalogs.create_account import (
    CreateAccount,
    create_account,
)
from track_anywhere.application.catalogs.create_asset import CreateAsset, create_asset
from track_anywhere.application.catalogs.create_book import CreateBook, create_book
from track_anywhere.application.catalogs.create_category import (
    CreateCategory,
    create_category,
)
from track_anywhere.application.catalogs.reopen_account import (
    AccountAlreadyActive,
    ReopenAccount,
    reopen_account,
)
from track_anywhere.application.idempotency import CommandActor
from track_anywhere.infrastructure.db.models.auth import BookMemberRecord, UserRecord
from track_anywhere.infrastructure.db.models.catalog import (
    AccountRecord,
    AssetRecord,
    BookRecord,
    CategoryRecord,
    CategoryVersionRecord,
)
from track_anywhere.infrastructure.db.models.event_store import BookEventHeadRecord
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


ACTOR = CommandActor(subject_id="human:catalog-command")


def _uow_factory(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(factory)


def _seed_actor(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            UserRecord.__table__.insert(),
            {
                "user_id": ACTOR.subject_id,
                "subject_type": "human",
                "current_display_name": "Catalog Owner",
                "status": "active",
            },
        )


def _create_book(engine):
    _seed_actor(engine)
    command = CreateBook(
        book_id=uuid4(),
        current_name="Household",
        base_asset_code=None,
    )
    result = create_book(
        command,
        actor=ACTOR,
        uow_factory=_uow_factory(engine),
    )
    return command, result


def test_create_book_commits_book_owner_and_zero_head_atomically(pg_engine) -> None:
    command, result = _create_book(pg_engine)

    assert result == {"book_id": str(command.book_id), "as_of_book_position": 0}
    with Session(pg_engine) as session:
        book = session.get(BookRecord, command.book_id)
        member = session.get(BookMemberRecord, (command.book_id, ACTOR.subject_id))
        head = session.get(BookEventHeadRecord, command.book_id)
        assert book is not None and book.write_state == "active"
        assert member is not None and member.role == "owner"
        assert member.scopes == [
            "book:read",
            "book:write",
            "ledger:read",
            "ledger:write",
        ]
        assert head is not None and (head.last_position, head.last_hash) == (
            0,
            bytes(32),
        )


def test_create_book_rolls_back_every_row_when_head_insert_fails(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_actor(pg_engine)
    command = CreateBook(book_id=uuid4(), current_name="Rollback", base_asset_code=None)

    from track_anywhere.application.catalogs import create_book as module

    def fail_head(*_args, **_kwargs):
        raise RuntimeError("forced head failure")

    monkeypatch.setattr(module, "_new_book_head", fail_head)
    with pytest.raises(RuntimeError, match="forced head failure"):
        create_book(
            command,
            actor=ACTOR,
            uow_factory=_uow_factory(pg_engine),
        )

    with Session(pg_engine) as session:
        assert session.get(BookRecord, command.book_id) is None
        assert (
            session.get(BookMemberRecord, (command.book_id, ACTOR.subject_id)) is None
        )
        assert session.get(BookEventHeadRecord, command.book_id) is None


def test_catalog_handlers_create_asset_account_and_versioned_category(
    pg_engine,
) -> None:
    book, _ = _create_book(pg_engine)
    asset = CreateAsset(
        book_id=book.book_id,
        asset_code="USD",
        kind="fiat",
        ledger_scale=2,
        input_scale=2,
        display_scale=2,
        current_name="US Dollar",
    )
    account = CreateAccount(
        book_id=book.book_id,
        account_id=uuid4(),
        asset_code="USD",
        account_type="asset",
        current_name="Cash",
    )
    category = CreateCategory(
        book_id=book.book_id,
        category_id=uuid4(),
        category_version_id=uuid4(),
        name="Food",
        parent_category_id=None,
        change_reason_code="created",
    )

    create_asset(asset, actor=ACTOR, uow_factory=_uow_factory(pg_engine))
    create_account(account, actor=ACTOR, uow_factory=_uow_factory(pg_engine))
    create_category(category, actor=ACTOR, uow_factory=_uow_factory(pg_engine))

    with Session(pg_engine) as session:
        assert session.get(AssetRecord, "USD") is not None
        assert (
            session.get(AccountRecord, (book.book_id, account.account_id)) is not None
        )
        current = session.get(CategoryRecord, (book.book_id, category.category_id))
        version = session.get(
            CategoryVersionRecord,
            (book.book_id, category.category_id, category.category_version_id),
        )
        assert (
            current is not None
            and current.current_version_id == category.category_version_id
        )
        assert version is not None and version.name == "Food"


def test_close_account_is_serialized_by_book_head_and_is_terminal(pg_engine) -> None:
    book, _ = _create_book(pg_engine)
    create_asset(
        CreateAsset(
            book_id=book.book_id,
            asset_code="USD",
            kind="fiat",
            ledger_scale=2,
            input_scale=2,
            display_scale=2,
            current_name="US Dollar",
        ),
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    account_id = uuid4()
    create_account(
        CreateAccount(
            book_id=book.book_id,
            account_id=account_id,
            asset_code="USD",
            account_type="asset",
            current_name="Cash",
        ),
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )

    result = close_account(
        CloseAccount(book_id=book.book_id, account_id=account_id),
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    assert result == {
        "account_id": str(account_id),
        "as_of_book_position": 0,
        "status": "closed",
    }
    with pytest.raises(AccountAlreadyClosed):
        close_account(
            CloseAccount(book_id=book.book_id, account_id=account_id),
            actor=ACTOR,
            uow_factory=_uow_factory(pg_engine),
        )
    reopened = reopen_account(
        ReopenAccount(book_id=book.book_id, account_id=account_id),
        actor=ACTOR,
        uow_factory=_uow_factory(pg_engine),
    )
    assert reopened == {
        "account_id": str(account_id),
        "as_of_book_position": 0,
        "status": "active",
    }
    with pytest.raises(AccountAlreadyActive):
        reopen_account(
            ReopenAccount(book_id=book.book_id, account_id=account_id),
            actor=ACTOR,
            uow_factory=_uow_factory(pg_engine),
        )
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AccountRecord)) == 1
        assert session.get(AccountRecord, (book.book_id, account_id)).status == "active"
