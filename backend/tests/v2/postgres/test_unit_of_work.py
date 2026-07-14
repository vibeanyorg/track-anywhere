from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from track_anywhere.application.unit_of_work import UnitOfWork
from track_anywhere.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


class TrackingSession(Session):
    close_called = False

    def close(self) -> None:
        self.close_called = True
        super().close()


class EnterFailureTransaction:
    def __enter__(self):
        raise RuntimeError("transaction enter failed")


class EntryFailureSession:
    def __init__(self, *, fail_in_begin: bool) -> None:
        self.fail_in_begin = fail_in_begin
        self.close_called = False

    def begin(self):
        if self.fail_in_begin:
            raise RuntimeError("session begin failed")
        return EnterFailureTransaction()

    def close(self) -> None:
        self.close_called = True


def _factory(pg_engine):
    return sessionmaker(
        bind=pg_engine,
        class_=TrackingSession,
        expire_on_commit=False,
    )


def test_unit_of_work_commits_and_always_closes_session(pg_engine) -> None:
    uow = SqlAlchemyUnitOfWork(_factory(pg_engine))

    with uow as entered:
        assert entered is uow
        assert isinstance(entered, UnitOfWork)
        entered.session.execute(
            text("""
            insert into assets (
                asset_code, kind, ledger_scale, input_scale, display_scale,
                current_name, status
            ) values ('USD', 'fiat', 2, 2, 2, 'US Dollar', 'active')
            """)
        )

    assert uow.session.close_called is True
    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text("select current_name from assets where asset_code = 'USD'")
            ).scalar_one()
            == "US Dollar"
        )


def test_unit_of_work_rolls_back_and_always_closes_session(pg_engine) -> None:
    uow = SqlAlchemyUnitOfWork(_factory(pg_engine))

    try:
        with uow:
            uow.session.execute(
                text("""
                insert into assets (
                    asset_code, kind, ledger_scale, input_scale, display_scale,
                    current_name, status
                ) values ('EUR', 'fiat', 2, 2, 2, 'Euro', 'active')
                """)
            )
            raise RuntimeError("force rollback")
    except RuntimeError as error:
        assert str(error) == "force rollback"

    assert uow.session.close_called is True
    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text("select count(*) from assets where asset_code = 'EUR'")
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("fail_in_begin", [True, False])
def test_unit_of_work_closes_session_when_transaction_entry_fails(
    fail_in_begin: bool,
) -> None:
    session = EntryFailureSession(fail_in_begin=fail_in_begin)
    uow = SqlAlchemyUnitOfWork(lambda: session)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="failed"):
        with uow:
            raise AssertionError("context body must not run")

    assert session.close_called is True
